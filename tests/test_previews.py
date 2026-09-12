import json
import logging
import threading
import time

import pytest
from conftest import CUTOFF, SLUG, approve, recording
from fsm.previews import PreviewService
from fsm.safety import Blocked
from fsm.storage import StorageBlocked
from test_planner import plan
from test_security_storage import client


def request(**values):
    return {"target": SLUG, "cameras": ["front"], "cutoff": CUTOFF, **values}


def complete(service, task):
    service.thread.join(10)
    assert not service.thread.is_alive()
    return service.get(task["id"], "admin")


def test_async_duplicate_and_reconnection_return_same_frozen_task(env, monkeypatch):
    import fsm.previews as previews

    recording(env)
    started, release = threading.Event(), threading.Event()
    original = previews.build_plan
    saved_id, confirmation = approve(env, plan(env))

    def delayed(*args, **kwargs):
        kwargs["progress"]("checking_files", processed=1, total=3)
        started.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(previews, "build_plan", delayed)
    c, headers = client(env)
    service = c.application.extensions["previews"]
    before = env.db.read_bytes()
    try:
        response = c.post("/api/preview", json=request(request_id="a" * 48), headers=headers)
        assert response.status_code == 202
        assert started.wait(5)
        duplicate = c.post("/api/preview", json=request(cameras=["side"]), headers=headers)
        assert duplicate.json["id"] == response.json["id"]
        assert duplicate.json["request"]["cameras"] == ["front"]
        # A fresh browser client reconnects through status, without the POST response.
        reopened = c.application.test_client()
        reopened.environ_base.update(c.environ_base)
        status = reopened.get("/api/status").json
        assert status["preview_busy"]
        assert status["previews"][0]["processed"] == 1
        assert status["previews"][0]["total"] == 3
        with pytest.raises(Blocked, match="Another user"):
            service.submit(request(), "other")
        with pytest.raises(Blocked, match="unavailable"):
            service.get(response.json["id"], "other")
        with pytest.raises(Blocked, match="read-only preview"):
            env.engine.submit(saved_id, "admin", confirmation, background=False)
        assert not env.store.jobs()
    finally:
        release.set()
        service.thread.join(10)
    task = service.get(response.json["id"], "admin")
    assert task["state"] == "completed", task
    replay = service.submit(request(request_id="a" * 48), "admin")
    assert replay["id"] == task["id"] and replay["state"] == "completed"
    assert env.db.read_bytes() == before
    assert ("stop", SLUG) not in env.supervisor.calls


def test_saved_result_survives_app_restart_and_items_are_owner_bound(env):
    recording(env)
    service = PreviewService(env.installation, env.storage, env.store)
    task = complete(service, service.submit(request(), "admin"))
    restarted = PreviewService(env.installation, env.storage, env.store)
    assert restarted.recent("admin")[0]["result"] == task["result"]
    assert restarted.recent("other") == []
    items = restarted.items(task["id"], "admin", "selected", "recordings", "r-old", 0)
    assert items["matched"] == 1
    assert items["items"][0]["files"][0]["path"] == "recordings/r-old.mp4"
    with pytest.raises(Blocked):
        restarted.items(task["id"], "other", "selected", "", "", 0)
    assert "signature" not in str(items)


def test_restart_reports_interrupted_worker_without_restarting_it(env):
    service = PreviewService(env.installation, env.storage, env.store)
    now = time.time()
    with env.store.connect() as db:
        db.execute(
            "INSERT INTO preview_tasks VALUES (?,?,?,?,?,?)",
            (
                "b" * 48,
                "admin",
                now,
                now,
                "running",
                json.dumps({"request": request(), "phase": "checking_files"}),
            ),
        )
    restarted = PreviewService(env.installation, env.storage, env.store)
    task = restarted.recent("admin")[0]
    assert task["state"] == "interrupted" and "restarted" in task["error"]
    assert not restarted.busy() and restarted.thread is None and service.thread is None


@pytest.mark.parametrize("error", [Blocked("Selection limit reached"), OSError("SECRET path")])
def test_failure_is_durable_and_logs_exclude_exception_payload(env, monkeypatch, caplog, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr("fsm.previews.build_plan", fail)
    caplog.set_level(logging.INFO, logger="fsm.preview")
    service = PreviewService(env.installation, env.storage, env.store)
    task = complete(service, service.submit(request(), "admin"))
    assert task["state"] == "failed"
    assert not service.busy()
    assert "SECRET" not in str(task) and "SECRET" not in caplog.text
    assert "phase=connecting" in caplog.text and "error_type=" in caplog.text
    assert "Selection limit" not in caplog.text


def test_storage_failure_details_survive_async_response(env, monkeypatch):
    details = {"media_path": "/media/frigate", "opened_mount": {"fstype": "ext4"}}

    def fail(*args, **kwargs):
        raise StorageBlocked("NFS access not confirmed", details)

    monkeypatch.setattr(env.storage, "validate", fail)
    service = PreviewService(env.installation, env.storage, env.store)
    task = complete(service, service.submit(request(), "admin"))
    assert task["diagnostics"] == details and task["failed_phase"] == "checking_storage"


def test_expiry_retention_and_filter_bounds(env):
    service = PreviewService(env.installation, env.storage, env.store)
    first = None
    for _ in range(5):
        task = complete(service, service.submit(request(), "admin"))
        first = first or task
        assert task["state"] == "completed", task
    assert len(service.recent("admin")) == 4
    with pytest.raises(Blocked):
        service.get(first["id"], "admin")
    with env.store.connect() as db:
        assert db.execute("SELECT count(*) FROM previews").fetchone()[0] == 4
        db.execute("UPDATE previews SET created=0")
    assert service.get(task["id"], "admin")["expired"]
    with pytest.raises(Blocked, match="expired"):
        service.items(task["id"], "admin", "selected", "", "", 0)
    for view, search, page in (("bad", "", 0), ("selected", "x" * 129, 0), ("selected", "", -1)):
        with pytest.raises(Blocked):
            service.items(task["id"], "admin", view, "", search, page)


def test_inspection_is_paginated_and_searches_saved_snapshot(env):
    for n in range(60):
        recording(env, id=f"recording-{n:03}")
    service = PreviewService(env.installation, env.storage, env.store)
    task = complete(service, service.submit(request(), "admin"))
    assert task["state"] == "completed", task
    assert len(service.items(task["id"], "admin", "selected", "", "", 0)["items"]) == 50
    assert len(service.items(task["id"], "admin", "selected", "", "", 1)["items"]) == 10
    # Subsequent changes to the live fixture cannot alter the saved inspection.
    import sqlite3

    with sqlite3.connect(env.db) as db:
        db.execute("DELETE FROM recordings")
    assert (
        service.items(task["id"], "admin", "selected", "recordings", "recording-059", 0)["matched"]
        == 1
    )


def test_invalid_scope_does_not_create_or_start_worker(env):
    service = PreviewService(env.installation, env.storage, env.store)
    for values in (
        {"cameras": []},
        {"cutoff": "bad"},
        {"include_exports": "yes"},
        {"request_id": "bad"},
    ):
        with pytest.raises(Blocked):
            service.submit(request(**values), "admin")
    assert not service.recent("admin") and service.thread is None


def test_worker_start_failure_releases_slot_and_leaves_durable_error(env, monkeypatch):
    service = PreviewService(env.installation, env.storage, env.store)

    def fail(*args):
        raise RuntimeError("synthetic thread start failure")

    monkeypatch.setattr(threading.Thread, "start", fail)
    with pytest.raises(Blocked, match="start"):
        service.submit(request(), "admin")
    assert not service.busy()
    assert service.recent("admin")[0]["state"] == "failed"


def test_item_api_filters_identity_and_release_gate(env):
    recording(env)
    c, headers = client(env)
    task = c.post("/api/preview", json=request(), headers=headers).json
    task = complete(c.application.extensions["previews"], task)
    path = f"/api/previews/{task['id']}/items"
    assert c.get(path + "?kind=recordings&search=r-old").json["matched"] == 1
    assert c.get(path + "?page=invalid").status_code == 409
    assert c.get(path, headers={"X-Remote-User-Id": "other"}).status_code == 409
    result = c.post(
        "/api/delete",
        json={"preview_id": task["preview_id"], "confirmation": task["confirmation"]},
        headers=headers,
    )
    assert result.status_code == 409 and "release-locked" in result.json["error"]
    assert not env.store.jobs()


def test_inspected_preview_remains_compatible_with_offline_cleanup(env):
    # The production HTTP gate remains off. Only this synthetic engine is enabled.
    recording(env)
    service = PreviewService(env.installation, env.storage, env.store)
    task = complete(service, service.submit(request(), "admin"))
    env.engine.submit(task["preview_id"], "admin", task["confirmation"], background=False)
    assert env.store.get(task["preview_id"])["phase"] == "completed"
