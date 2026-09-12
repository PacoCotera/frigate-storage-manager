import json
import os
import sqlite3
import subprocess
import sys
import threading
import tracemalloc
from pathlib import Path

import pytest
from conftest import SLUG, event, ids, insert, media, recording, review
from crash_worker import engine_for
from fsm.reset import RESET_PHRASE, Reset, prepare_reset, tree_entries
from fsm.safety import Blocked, read_json
from test_jobs import Crash, crash_at, fresh_engine
from test_maintenance import wait_done
from test_security_storage import client


def reset_preview(env):
    return prepare_reset(env.installation, env.storage, env.store, SLUG, "admin")


def reset_fixture(env):
    recording(env)
    recording(env, "other-camera", camera="side")
    event(env, bookmark=1)
    review(env, events=["e-old"])
    media(env, "clips/faces/known-person/face.webp")
    media(env, "exports/keep-by-default.mp4")
    media(env, "unrelated-share-data/preserve.txt", b"preserve")
    insert(env, "user", username="old-frigate-user")


def submit_reset(env, approved=None):
    request = approved or reset_preview(env)
    return env.engine.submit(
        request["preview_id"], "admin", request["confirmation"], background=False, kind="reset"
    )


def test_full_reset_removes_all_cameras_bookmarks_media_database_but_not_configuration(env):
    reset_fixture(env)
    config = (env.db.parent / "config.yml").read_bytes()
    model = env.db.parent / "model_cache/model.bin"
    model.parent.mkdir()
    model.write_bytes(b"preserve model")
    approved = reset_preview(env)
    assert env.db.exists() and not env.store.jobs()  # Preparation is read-only.
    assert ("stop", SLUG) not in env.supervisor.calls
    job = submit_reset(env, approved)
    assert job["phase"] == "completed", job
    assert not env.db.exists()
    for name in ("recordings", "clips", "exports"):
        assert not (env.root / name).exists()
    for suffix in ("-wal", "-shm", "-journal"):
        assert not Path(str(env.db) + suffix).exists()
    assert (env.root / "unrelated-share-data/preserve.txt").read_bytes() == b"preserve"
    assert model.read_bytes() == b"preserve model"
    assert (env.db.parent / "config.yml").read_bytes() == config
    assert env.supervisor.app["state"] == "started"
    with sqlite3.connect(env.root / job["backup"]) as db:
        assert db.execute("SELECT retain_indefinitely FROM event").fetchone()[0] == 1
        assert db.execute("SELECT username FROM user").fetchone()[0] == "old-frigate-user"
    db.close()
    env.engine.remove_backup(job["id"])
    assert not (env.root / job["backup"]).exists()
    assert not Reset(env.engine).local_folder(job).exists()


@pytest.mark.parametrize(
    "phase,committed",
    [
        ("stopping", False),
        ("reset_backup", False),
        ("reset_staging", False),
        ("reset_after_media_stage", False),
        ("reset_checking", False),
        ("reset_after_database_stage", False),
        ("reset_ready", False),
        ("before_reset_commit", False),
        ("after_reset_commit", True),
        ("reset_committed", True),
        ("reset_purging", True),
        ("reset_after_purge", True),
        ("reset_after_database_finish", True),
        ("restarting", True),
    ],
)
@pytest.mark.parametrize("running", [True, False])
def test_reset_recovers_each_interruption_and_original_lifecycle(env, phase, committed, running):
    reset_fixture(env)
    env.supervisor.app["state"] = "started" if running else "stopped"
    approved = reset_preview(env)
    env.engine.hook = crash_at(phase)
    with pytest.raises(Crash):
        submit_reset(env, approved)
    job = fresh_engine(env).recover(approved["preview_id"])
    assert job["phase"] == ("completed" if committed else "rolled_back"), job
    assert env.db.exists() is not committed
    assert (env.root / "recordings/r-old.mp4").exists() is not committed
    assert (env.root / "clips/faces/known-person/face.webp").exists() is not committed
    assert (env.root / "exports/keep-by-default.mp4").exists() is not committed
    if not committed:
        assert ids(env, "event") == ["e-old"]
    assert env.supervisor.app["state"] == ("started" if running else "stopped")


def test_reset_http_requires_typed_confirmation_and_cannot_use_cleanup_route(env):
    reset_fixture(env)
    c, headers = client(env)
    approved = c.post("/api/reset/prepare", json={"target": SLUG}, headers=headers).json
    assert approved["phrase"] == RESET_PHRASE
    assert (
        c.post("/api/reset", json=approved | {"phrase": "wrong"}, headers=headers).status_code
        == 409
    )
    assert c.post("/api/delete", json=approved, headers=headers).status_code == 409
    assert (
        c.post(
            "/api/reset", json=approved, headers=headers | {"X-Remote-User-Id": "reader"}
        ).status_code
        == 403
    )
    assert not env.store.jobs()
    done = threading.Event()
    env.engine.hook = lambda phase: done.set() if phase == "completed" else None
    response = c.post("/api/reset", json=approved, headers=headers)
    assert response.status_code == 202, response.json
    wait_done(env, done)
    assert not env.db.exists()
    assert c.post("/api/reset", json=approved, headers=headers).status_code == 409
    status = c.get("/api/status").json
    assert status["jobs"][0]["kind"] == "reset" and not status["recovery_required"]


@pytest.mark.parametrize("route", ["reset", "reset/prepare"])
def test_reset_default_allowlist_and_csrf_block_requests(env, route):
    c, headers = client(env, "reader")
    assert c.post(f"/api/{route}", json={}, headers=headers).status_code == 409
    c, _ = client(env)
    assert c.post(f"/api/{route}", json={}).status_code == 403
    assert not env.store.jobs()


def test_reset_marker_or_mount_change_leaves_frigate_stopped(env):
    reset_fixture(env)
    request = reset_preview(env)
    env.engine.hook = crash_at("reset_committed")
    with pytest.raises(Crash):
        submit_reset(env, request)
    job = env.store.get(request["preview_id"])
    marker = Reset(env.engine).local_folder(job) / "committed.json"
    original = marker.read_bytes()
    marker.unlink()
    with pytest.raises(Blocked, match="ambiguous"):
        fresh_engine(env).recover(job["id"])
    marker.write_bytes(original)
    env.storage.identity["source"] = "different:/media"
    with pytest.raises(Blocked, match="identity"):
        fresh_engine(env).recover(job["id"])
    assert env.supervisor.app["state"] == "stopped"


def test_reset_unexpected_media_and_database_after_commit_are_not_overwritten(env):
    reset_fixture(env)
    env.engine.hook = crash_at("reset_committed")
    request = reset_preview(env)
    with pytest.raises(Crash):
        submit_reset(env, request)
    (env.root / "recordings").mkdir()
    with pytest.raises(Blocked, match="reappeared"):
        fresh_engine(env).recover(request["preview_id"])
    (env.root / "recordings").rmdir()
    env.db.write_bytes(b"new database, never overwrite")
    with pytest.raises(Blocked, match="replaced"):
        fresh_engine(env).recover(request["preview_id"])
    assert env.db.read_bytes() == b"new database, never overwrite"
    assert env.supervisor.app["state"] == "stopped"


def test_reset_unsafe_nested_media_rolls_back_before_commit(env):
    reset_fixture(env)
    os.link(env.root / "recordings/r-old.mp4", env.root / "recordings/duplicate.mp4")
    job = submit_reset(env)
    assert job["phase"] == "rolled_back", job
    assert env.db.exists() and (env.root / "recordings/r-old.mp4").exists()
    assert env.supervisor.app["state"] == "started"


def test_reset_streams_large_directory_without_an_in_memory_manifest(env):
    folder = env.root / "recordings"
    folder.mkdir()
    for index in range(3000):
        (folder / f"{index}.mp4").write_bytes(b"synthetic")
    tracemalloc.start()
    count = sum(
        1
        for kind, _, _ in tree_entries(env.root, "recordings", folder.stat().st_dev)
        if kind == "file"
    )
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert count == 3000
    assert peak < 2 * 1024 * 1024


@pytest.mark.parametrize(
    "phase,committed",
    [
        ("reset_after_media_stage", False),
        ("reset_after_database_stage", False),
        ("after_reset_commit", True),
        ("reset_after_purge", True),
    ],
)
def test_reset_actual_process_exit_recovers_staged_database_and_media(env, phase, committed):
    reset_fixture(env)
    root = env.data.parent
    (root / "supervisor-state.json").write_text(json.dumps({"state": "started"}))
    request = reset_preview(env)
    repo = Path(__file__).resolve().parents[1]
    process = subprocess.run(
        [
            sys.executable,
            str(repo / "tests/crash_worker.py"),
            str(root),
            phase,
            request["preview_id"],
            request["confirmation"],
            "reset",
        ],
        env=os.environ
        | {
            "PYTHONPATH": os.pathsep.join(
                [str(repo / "frigate_storage_manager"), str(repo / "tests")]
            )
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 77, process.stdout + process.stderr
    job = engine_for(root).recover(request["preview_id"])
    assert job["phase"] == ("completed" if committed else "rolled_back"), job
    assert env.db.exists() is not committed
    assert (env.root / "recordings/r-old.mp4").exists() is not committed
    assert json.loads((root / "supervisor-state.json").read_text())["state"] == "started"


def test_reset_stages_real_wal_and_preserves_it_on_rollback(env):
    recording(env)
    insert(env, "user", username="before-wal")
    code = "import sqlite3,os,sys; db=sqlite3.connect(sys.argv[1]); db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA wal_autocheckpoint=0'); db.execute(\"UPDATE user SET username='wal-user'\"); db.commit(); os._exit(0)"
    result = subprocess.run(
        [sys.executable, "-c", code, str(env.db)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    assert Path(str(env.db) + "-wal").exists()
    request = reset_preview(env)
    env.engine.hook = crash_at("before_reset_commit")
    with pytest.raises(Crash):
        submit_reset(env, request)
    job = env.store.get(request["preview_id"])
    manifest = read_json(env.root / job["manifest"])
    assert any(entry["path"].endswith("-wal") for entry in manifest["database_files"])
    fresh_engine(env).recover(job["id"])
    with sqlite3.connect(env.db) as db:
        assert db.execute("SELECT username FROM user").fetchone()[0] == "wal-user"
    db.close()


def test_reset_retries_failed_restart_without_touching_recreated_database(env):
    reset_fixture(env)
    original = env.supervisor.lifecycle

    def start_then_lose_response(slug, action):
        if action == "start":
            env.db.write_bytes(b"new database from Frigate, preserve on recovery")
            raise Blocked("Synthetic lost start response")
        original(slug, action)

    env.supervisor.lifecycle = start_then_lose_response
    job = submit_reset(env)
    assert job["phase"] == "restarting"
    env.supervisor.lifecycle = original
    recovered = fresh_engine(env).recover(job["id"])
    assert recovered["phase"] == "completed"
    assert env.db.read_bytes() == b"new database from Frigate, preserve on recovery"


def test_cleanup_confirmation_cannot_authorize_full_reset(env):
    from conftest import approve
    from test_planner import plan

    recording(env)
    token, confirmation = approve(env, plan(env))
    c, headers = client(env)
    response = c.post(
        "/api/reset",
        json={"preview_id": token, "confirmation": confirmation, "phrase": RESET_PHRASE},
        headers=headers,
    )
    assert response.status_code == 409 and "different operation" in response.json["error"]
    assert not env.store.jobs() and env.db.exists()


def test_reset_symlink_inside_media_cannot_escape_to_other_files(env, tmp_path):
    reset_fixture(env)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"not Frigate data")
    try:
        (env.root / "clips/escape").symlink_to(outside)
    except OSError:
        pytest.skip("Windows host has no symlink privilege; required in Linux CI")
    job = submit_reset(env)
    assert job["phase"] == "rolled_back", job
    assert outside.read_bytes() == b"not Frigate data"
    assert env.db.exists() and env.supervisor.app["state"] == "started"
