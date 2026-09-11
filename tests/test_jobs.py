import json
import sqlite3
import threading

import pytest
from conftest import SLUG, T, approve, event, ids, insert, media, recording, review
from fsm.database import load_vectors
from fsm.jobs import Engine, JobStore
from fsm.safety import Blocked, read_json
from test_planner import plan


class Crash(BaseException):
    """Simulated process termination: bypass normal exception recovery."""


def crash_at(phase):
    def hook(value):
        if value == phase:
            raise Crash(phase)

    return hook


def submit(env, result=None):
    token, confirmation = approve(env, result or plan(env))
    env.engine.submit(token, "admin", confirmation, background=False)
    return env.store.get(token)


def fresh_engine(env):
    return Engine(JobStore(env.data), env.installation, env.storage, gate=lambda: True)


def test_complete_cleanup_preserves_configuration_users_and_backup(env):
    recording(env)
    event(env)
    review(env, events=["e-old"])
    insert(env, "timeline", source_id="e-old")
    insert(env, "userreviewstatus", review_segment_id="review-old", user_id="admin")
    insert(env, "user", username="untouched")
    before = (env.db.parent / "config.yml").read_bytes()
    job = submit(env)
    assert job["phase"] == "completed", job
    for table in ("recordings", "event", "reviewsegment", "userreviewstatus"):
        assert not ids(env, table)
    assert not (env.root / "recordings/r-old.mp4").exists()
    assert env.supervisor.app["state"] == "started"
    assert (env.db.parent / "config.yml").read_bytes() == before
    backup = env.root / job["backup"]
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT count(*) FROM recordings").fetchone()[0] == 1
    with sqlite3.connect(env.db) as db:
        assert db.execute("SELECT username FROM user").fetchone()[0] == "untouched"
    assert job["media_bytes_removed"] > 0


@pytest.mark.parametrize(
    "phase",
    [
        "stopping",
        "stopped",
        "backup",
        "prepared",
        "staging",
        "before_stage",
        "after_stage",
        "committing",
        "database_update",
        "before_commit",
    ],
)
@pytest.mark.parametrize("running", [True, False])
def test_restart_before_commit_restores_media_and_original_state(env, phase, running):
    recording(env)
    event(env)
    env.supervisor.app["state"] = "started" if running else "stopped"
    env.engine.hook = crash_at(phase)
    token, confirmation = approve(env, plan(env))
    with pytest.raises(Crash):
        env.engine.submit(token, "admin", confirmation, background=False)
    other = fresh_engine(env)
    assert other.store.get(token)["phase"] not in ("completed", "rolled_back")
    result = other.recover(token)
    assert result["phase"] == "rolled_back", result
    assert ids(env, "recordings") == ["r-old"]
    assert ids(env, "event") == ["e-old"]
    assert (env.root / "recordings/r-old.mp4").read_bytes() == b"synthetic media"
    assert env.supervisor.app["state"] == ("started" if running else "stopped")


@pytest.mark.parametrize(
    "phase", ["after_commit", "committed", "purging", "after_purge", "restarting"]
)
@pytest.mark.parametrize("running", [True, False])
def test_restart_after_commit_finishes_deletion(env, phase, running):
    recording(env)
    env.supervisor.app["state"] = "started" if running else "stopped"
    env.engine.hook = crash_at(phase)
    token, confirmation = approve(env, plan(env))
    with pytest.raises(Crash):
        env.engine.submit(token, "admin", confirmation, background=False)
    result = fresh_engine(env).recover(token)
    assert result["phase"] == "completed", result
    assert not ids(env, "recordings")
    assert not (env.root / "recordings/r-old.mp4").exists()
    assert env.supervisor.app["state"] == ("started" if running else "stopped")


@pytest.mark.parametrize(
    "phase,expected",
    [
        ("after_stage", "rolled_back"),
        ("database_update", "rolled_back"),
        ("before_commit", "rolled_back"),
        ("after_commit", "completed"),
    ],
)
def test_normal_faults_and_lost_commit_response_automatically_resolve(env, phase, expected):
    recording(env)

    def hook(value):
        if value == phase:
            raise OSError("injected failure")

    env.engine.hook = hook
    result = submit(env)
    assert result["phase"] == expected, result
    assert env.supervisor.app["state"] == "started"


def test_scope_expansion_demands_fresh_confirmation(env):
    recording(env)
    approved = plan(env)
    recording(env, "new-old", T - 200, T - 190)
    job = submit(env, approved)
    assert job["phase"] == "needs_preview", job
    assert ids(env, "recordings") == ["new-old", "r-old"]
    assert env.supervisor.app["state"] == "started"


def test_bookmark_added_after_preview_shrinks_scope_safely(env):
    recording(env)
    event(env)
    approved = plan(env)
    with sqlite3.connect(env.db) as db:
        db.execute("UPDATE event SET retain_indefinitely=1")
    job = submit(env, approved)
    assert job["phase"] == "completed", job
    assert ids(env, "recordings") == ["r-old"]
    assert ids(env, "event") == ["e-old"]


def test_mount_identity_change_blocks_recovery_and_restart(env):
    recording(env)
    env.engine.hook = crash_at("after_stage")
    token, confirmation = approve(env, plan(env))
    with pytest.raises(Crash):
        env.engine.submit(token, "admin", confirmation, background=False)
    env.storage.identity["source"] = "different:/share"
    with pytest.raises(Blocked, match="identity"):
        fresh_engine(env).recover(token)
    assert env.supervisor.app["state"] == "stopped"


def test_missing_commit_witness_is_ambiguous_and_keeps_frigate_stopped(env):
    recording(env)
    env.engine.hook = crash_at("committed")
    token, confirmation = approve(env, plan(env))
    with pytest.raises(Crash):
        env.engine.submit(token, "admin", confirmation, background=False)
    with sqlite3.connect(env.db) as db:
        db.execute("DELETE FROM _fsm_commit")
    with pytest.raises(Blocked, match="ambiguous"):
        fresh_engine(env).recover(token)
    assert env.supervisor.app["state"] == "stopped"


def test_corrupt_manifest_and_missing_staged_files_do_not_restart(env):
    recording(env)
    env.engine.hook = crash_at("after_stage")
    token, confirmation = approve(env, plan(env))
    with pytest.raises(Crash):
        env.engine.submit(token, "admin", confirmation, background=False)
    job = env.store.get(token)
    path = env.root / job["manifest"]
    original = path.read_bytes()
    data = read_json(path)
    data["target"] = "unrelated"
    path.write_text(json.dumps(data))
    with pytest.raises(Blocked, match="integrity"):
        fresh_engine(env).recover(token)
    path.write_bytes(original)
    (env.root / env.engine.staged(job, 0)).unlink()
    with pytest.raises(Blocked, match="missing"):
        fresh_engine(env).recover(token)
    assert env.supervisor.app["state"] == "stopped"


def test_double_submission_replay_and_concurrent_jobs(env):
    recording(env)
    a, signature = approve(env, plan(env))
    b, second_signature = approve(env, plan(env))
    paused, resume = threading.Event(), threading.Event()

    def hook(phase):
        if phase == "stopped":
            paused.set()
            assert resume.wait(10)

    env.engine.hook = hook
    env.engine.submit(a, "admin", signature)
    assert paused.wait(5)
    try:
        with pytest.raises(Blocked, match="active"):
            env.engine.submit(a, "admin", signature)
        with pytest.raises(Blocked, match="active"):
            fresh_engine(env).submit(b, "admin", second_signature)
    finally:
        resume.set()
    assert env.engine.lock.acquire(timeout=10)
    env.engine.lock.release()
    with pytest.raises(Blocked, match="used"):
        env.engine.submit(a, "admin", signature)


@pytest.mark.parametrize(
    "option,value", [("boot", "auto"), ("watchdog", True), ("auto_update", True)]
)
def test_reboot_safety_preconditions_no_stop(env, option, value):
    recording(env)
    env.supervisor.app[option] = value
    result = submit(env)
    assert result["phase"] == "rejected", result
    assert env.supervisor.app["state"] == "started"
    assert ("stop", SLUG) not in env.supervisor.calls


def test_nfs_readonly_prevents_stop(env):
    recording(env)
    env.storage.readonly = True
    result = submit(env)
    assert result["phase"] == "rejected"
    assert ("stop", SLUG) not in env.supervisor.calls


def test_supervisor_start_failure_recoverable_without_repeating_cleanup(env):
    recording(env)
    env.supervisor.fail_action = "start"
    result = submit(env)
    assert result["phase"] == "restarting", result
    assert result["recovery_required"]
    assert not ids(env, "recordings")
    env.supervisor.fail_action = None
    assert fresh_engine(env).recover(result["id"])["phase"] == "completed"


def test_vectors_and_exports_are_transactionally_removed(env):
    recording(env)
    event(env)
    for id, busy in (("old-export", 0), ("preserved-busy", 1)):
        insert(
            env,
            "export",
            id=id,
            camera="side" if busy else "front",
            in_progress=busy,
            video_path=media(env, f"exports/{id}.mp4"),
            thumb_path=media(env, f"clips/export/{id}.webp"),
        )
    with sqlite3.connect(env.db) as db:
        load_vectors(db)
        for table, col in (
            ("vec_thumbnails", "thumbnail_embedding"),
            ("vec_descriptions", "description_embedding"),
        ):
            db.execute(
                f"CREATE VIRTUAL TABLE {table} USING vec0(id TEXT PRIMARY KEY,{col} FLOAT[768] distance_metric=cosine)"
            )
            db.execute(f"INSERT INTO {table} VALUES (?,?)", ("e-old", json.dumps([0.1] * 768)))
    job = submit(env, plan(env, include_exports=True))
    assert job["phase"] == "completed", job
    assert ids(env, "export") == ["preserved-busy"]
    with sqlite3.connect(env.db) as db:
        load_vectors(db)
        assert db.execute("SELECT count(*) FROM vec_thumbnails").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM vec_descriptions").fetchone()[0] == 0


def test_backup_lifecycle_is_explicit_and_bounded(env):
    recording(env)
    job = submit(env)
    backup = env.root / job["backup"]
    assert backup.exists()
    env.engine.remove_backup(job["id"])
    assert not backup.exists()
    assert env.store.get(job["id"])["backup_removed"]


def test_release_gate_precedes_all_mutations(env):
    recording(env)
    engine = Engine(env.store, env.installation, env.storage)
    token, signature = approve(env, plan(env))
    with pytest.raises(Blocked, match="disabled"):
        engine.submit(token, "admin", signature)
    assert not env.store.jobs()
    assert ("stop", SLUG) not in env.supervisor.calls
