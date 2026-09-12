import threading

import pytest
from conftest import CUTOFF, SLUG, T, approve, event, ids, recording, review
from fsm import DESTRUCTIVE_ENABLED
from fsm.jobs import Engine
from fsm.safety import Blocked
from fsm.storage import probe_directory
from test_jobs import Crash, crash_at
from test_planner import plan
from test_security_storage import client


def selection(env, c, headers):
    response = c.post(
        "/api/preview",
        json={"target": SLUG, "cameras": ["front"], "cutoff": CUTOFF},
        headers=headers,
    )
    assert response.status_code == 202
    c.application.extensions["previews"].thread.join(10)
    task = c.get(f"/api/previews/{response.json['id']}").json
    assert task["state"] == "completed", task
    return {"preview_id": task["preview_id"], "confirmation": task["confirmation"]}


def wait_done(env, done):
    assert done.wait(10), env.store.jobs()
    assert env.engine.lock.acquire(timeout=10)
    env.engine.lock.release()


def test_enabled_http_cleanup_preserves_bookmark_group_and_rejects_replay(env):
    assert DESTRUCTIVE_ENABLED
    recording(env)
    event(env)
    review(env, events=["e-old"])
    recording(env, "protected", T - 300, T - 290)
    event(env, "bookmark", T - 300, T - 290, bookmark=1)
    review(env, "kept-review", ["bookmark"], T - 300, T - 290)
    before_config = (env.db.parent / "config.yml").read_bytes()
    c, headers = client(env)
    assert c.post("/api/validate", json={"target": SLUG}, headers=headers).json["maintenance"][
        "ready"
    ]
    request = selection(env, c, headers)
    done = threading.Event()
    env.engine.hook = lambda phase: done.set() if phase == "completed" else None
    response = c.post("/api/delete", json=request, headers=headers)
    assert response.status_code == 202, response.json
    wait_done(env, done)
    assert ids(env, "recordings") == ["protected"]
    assert ids(env, "event") == ["bookmark"]
    assert ids(env, "reviewsegment") == ["kept-review"]
    assert (env.root / "recordings/protected.mp4").exists()
    assert not (env.root / "recordings/r-old.mp4").exists()
    assert (env.db.parent / "config.yml").read_bytes() == before_config
    assert env.supervisor.app["state"] == "started"
    job = c.get("/api/status").json["jobs"][0]
    assert job["phase"] == "completed" and job["media_bytes_removed"] > 0
    assert (env.root / job["backup"]).exists()
    assert c.post("/api/delete", json=request, headers=headers).status_code == 409
    response = c.post("/api/backups/remove", json={"job_id": job["id"]}, headers=headers)
    assert response.status_code == 200 and response.json["backup_removed"]


@pytest.mark.parametrize("route", ["delete", "recover", "backups/remove", "probe"])
def test_enabled_mutations_require_allowlist_and_csrf(env, route):
    c, headers = client(env, "reader")
    assert c.post(f"/api/{route}", json={}, headers=headers).status_code == 409
    c, _ = client(env)
    assert c.post(f"/api/{route}", json={}).status_code == 403
    assert not env.store.jobs()
    assert ("stop", SLUG) not in env.supervisor.calls


@pytest.mark.parametrize("failure", ["readonly", "boot", "rename"])
def test_http_cleanup_rechecks_access_and_settings_before_stop(env, monkeypatch, failure):
    recording(env)
    c, headers = client(env)
    request = selection(env, c, headers)
    if failure == "readonly":
        env.storage.readonly = True
    elif failure == "boot":
        env.supervisor.app["boot"] = "auto"
    else:

        def denied(*args):
            raise PermissionError("synthetic rename denial")

        monkeypatch.setattr("fsm.storage.move_file", denied)
    done = threading.Event()
    env.engine.hook = lambda phase: done.set() if phase == "rejected" else None
    response = c.post("/api/delete", json=request, headers=headers)
    assert response.status_code == 202
    wait_done(env, done)
    assert env.store.jobs()[0]["phase"] == "rejected"
    assert ids(env, "recordings") == ["r-old"]
    assert ("stop", SLUG) not in env.supervisor.calls
    assert not list(env.root.glob(".fsm-probe-*"))


@pytest.mark.parametrize(
    "phase,outcome", [("after_stage", "rolled_back"), ("after_commit", "completed")]
)
def test_enabled_http_recovery_after_process_interruption(env, phase, outcome):
    recording(env)
    token, confirmation = approve(env, plan(env))
    env.engine.hook = crash_at(phase)
    with pytest.raises(Crash):
        env.engine.submit(token, "admin", confirmation, background=False)
    assert env.supervisor.app["state"] == "stopped"
    env.engine = Engine(env.store, env.installation, env.storage, gate=lambda: DESTRUCTIVE_ENABLED)
    done = threading.Event()
    env.engine.hook = lambda value: done.set() if value == outcome else None
    c, headers = client(env)
    assert c.get("/api/status").json["recovery_required"]
    response = c.post("/api/recover", json={"job_id": token}, headers=headers)
    assert response.status_code == 202
    wait_done(env, done)
    assert env.store.get(token)["phase"] == outcome
    assert bool(ids(env, "recordings")) == (outcome == "rolled_back")
    assert env.supervisor.app["state"] == "started"


def test_validation_explains_each_cleanup_blocker_without_blocking_preview(env):
    env.supervisor.app.update(boot="auto", watchdog=True, auto_update=True)
    env.storage.readonly = True
    c, headers = client(env)
    response = c.post("/api/validate", json={"target": SLUG}, headers=headers)
    assert response.status_code == 200
    readiness = response.json["maintenance"]
    assert not readiness["ready"]
    for text in ("Start on boot", "Watchdog", "Auto update", "read-only"):
        assert text in " ".join(readiness["blockers"])
    assert ("stop", SLUG) not in env.supervisor.calls


def test_probe_rename_failure_after_move_removes_only_disposable_file(env, monkeypatch):
    from fsm.storage import move_file

    recording(env)

    def lost_response(*args):
        move_file(*args)
        raise OSError("synthetic fsync failure after rename")

    monkeypatch.setattr("fsm.storage.move_file", lost_response)
    with pytest.raises(OSError):
        probe_directory(env.root)
    assert not list(env.root.glob(".fsm-probe-*"))
    assert (env.root / "recordings/r-old.mp4").read_bytes() == b"synthetic media"


def test_worker_start_failure_is_resolved_and_releases_lock(env, monkeypatch):
    recording(env)
    token, confirmation = approve(env, plan(env))

    def unavailable(*args):
        raise RuntimeError("synthetic worker failure")

    monkeypatch.setattr(threading.Thread, "start", unavailable)
    with pytest.raises(Blocked, match="Could not start"):
        env.engine.submit(token, "admin", confirmation)
    assert env.store.get(token)["phase"] == "rejected"
    assert not env.engine.lock.locked()
    assert ("stop", SLUG) not in env.supervisor.calls
