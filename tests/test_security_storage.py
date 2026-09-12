import os
import sqlite3
from types import SimpleNamespace

import pytest
from conftest import CUTOFF, SLUG, media, recording
from fsm.integration import NoRedirect, Supervisor, project_config
from fsm.safety import Blocked, relative, safe_path
from fsm.storage import Storage, mount_entries
from fsm.web import create_app
from test_planner import plan


def client(env, user="admin"):
    app = create_app(env.installation, env.storage, env.store, env.engine)
    app.testing = True
    c = app.test_client()
    c.environ_base["REMOTE_ADDR"] = "172.30.32.2"
    c.environ_base["HTTP_X_REMOTE_USER_ID"] = user
    status = c.get("/api/status").json
    return c, {"X-FSM-CSRF": status["csrf"]}


def test_ingress_validation_preview_and_disabled_delete(env):
    recording(env)
    c, headers = client(env)
    assert c.get("/").status_code == 200
    assert c.get("/static/app.js").status_code == 200
    module = c.get("/static/time.mjs")
    assert module.status_code == 200 and module.mimetype == "text/javascript"
    assert c.get("/api/discovery").json["apps"][0]["slug"] == SLUG
    response = c.post("/api/validate", json={"target": SLUG}, headers=headers)
    assert response.status_code == 200, response.json
    assert response.json["lifecycle"]["stop_start_tested"] is False
    response = c.post(
        "/api/preview",
        json={"target": SLUG, "cameras": ["front"], "cutoff": CUTOFF},
        headers=headers,
    )
    assert response.status_code == 200, response.json
    assert response.json["result"]["counts"]["recordings"] == 1
    assert "rows" not in response.json["result"]
    assert c.post("/api/delete", json=response.json, headers=headers).status_code == 409
    assert not env.store.jobs()
    assert ("stop", SLUG) not in env.supervisor.calls


def test_untrusted_peer_forwarded_address_and_missing_user(env):
    c, _ = client(env)
    assert (
        c.get(
            "/api/status",
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            headers={"X-Forwarded-For": "172.30.32.2"},
        ).status_code
        == 403
    )
    assert c.get("/api/status", headers={"X-Remote-User-Id": ""}).status_code == 403


def test_csrf_cross_origin_content_type_and_user_binding(env):
    c, headers = client(env)
    assert c.post("/api/preview", json={}).status_code == 403
    assert (
        c.post(
            "/api/preview", json={}, headers=headers | {"Sec-Fetch-Site": "cross-site"}
        ).status_code
        == 403
    )
    assert c.post("/api/preview", data="{}", headers=headers).status_code == 403
    assert (
        c.post(
            "/api/preview", json={}, headers=headers | {"X-Remote-User-Id": "different"}
        ).status_code
        == 403
    )
    assert c.post("/api/preview", json={}, headers={"X-FSM-CSRF": "0.bad"}).status_code == 403


def test_non_admin_cannot_probe_but_can_preview(env):
    c, headers = client(env, "reader")
    response = c.post("/api/probe", json={"target": SLUG}, headers=headers)
    assert response.status_code == 409
    assert "admin_user_ids" in response.json["error"]
    assert not c.get("/api/status").json["is_admin"]


def test_probe_is_disposable_and_does_not_touch_database(env):
    before = env.db.read_bytes()
    c, headers = client(env)
    response = c.post("/api/probe", json={"target": SLUG}, headers=headers)
    assert response.status_code == 200
    assert env.db.read_bytes() == before
    assert not list(env.root.glob(".fsm-probe-*"))
    assert not list(env.db.parent.glob(".fsm-probe-*"))


def test_credentials_are_projected_out_of_effective_config(env):
    config = env.supervisor.config | {
        "mqtt": {"password": "secret-camera-password"},
        "auth": {"reset_admin_password": True},
    }
    assert "secret-camera-password" not in str(project_config(config))
    env.supervisor.config = config
    c, headers = client(env)
    response = c.post("/api/validate", json={"target": SLUG}, headers=headers)
    assert "secret-camera-password" not in response.get_data(as_text=True)
    assert "secret-camera-password" not in (env.data / f"config-{SLUG}.json").read_text()


@pytest.mark.parametrize(
    "path",
    [
        "../other/frigate.db",
        "/etc/passwd",
        "x/../../passwd",
        "a\\b",
        "a//b",
        "C:/data",
        "a/./b",
        "x\nfile",
    ],
)
def test_path_traversal_blocked(path):
    with pytest.raises(Blocked):
        relative(path)


def test_selected_app_scope_and_database_match(env):
    with pytest.raises(Blocked):
        env.installation.resolve("unrelated")
    env.installation.options["database_relative_path"] = "wrong.db"
    with pytest.raises(Blocked, match="does not match"):
        env.installation.resolve(SLUG)


def test_ambiguous_app_selection_never_guesses(env):
    env.installation.options["target_slug"] = ""
    with pytest.raises(Blocked, match="explicitly"):
        env.installation.target()


def test_ambiguous_configuration_and_config_change_while_stopped(env):
    (env.db.parent / "config.yaml").write_text("second file")
    with pytest.raises(Blocked, match="exactly one"):
        env.installation.resolve(SLUG)
    (env.db.parent / "config.yaml").unlink()
    env.supervisor.app["state"] = "stopped"
    (env.db.parent / "config.yml").write_text("changed")
    with pytest.raises(Blocked, match="changed"):
        env.installation.resolve(SLUG)


def test_symlink_escape_blocked(env, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "r-old.mp4").write_bytes(b"preserve")
    try:
        (env.root / "recordings").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Windows host has no symlink privilege; required in Linux CI")
    with pytest.raises(Blocked, match="Symlinks"):
        safe_path(env.root, "recordings/r-old.mp4")
    assert (outside / "r-old.mp4").read_bytes() == b"preserve"


def test_hardlinked_indexed_file_blocked(env):
    recording(env)
    os.link(env.root / "recordings/r-old.mp4", env.root / "second-link")
    with pytest.raises(Blocked, match="one link"):
        plan(env)


def test_indexed_path_outside_expected_category_blocked(env):
    recording(env)
    path = media(env, "clips/faces/training.jpg")
    with sqlite3.connect(env.db) as db:
        db.execute("UPDATE recordings SET path=?", (path,))
    with pytest.raises(Blocked, match="category"):
        plan(env)


def test_mountinfo_escapes_and_nested_mount_selection():
    line = "55 23 0:40 / /media/frigate rw,relatime - nfs4 store:/camera\\040media rw,vers=4.2"
    result = list(mount_entries(line))[0]
    assert result["source"] == "store:/camera media"
    assert result["fstype"] == "nfs4"


@pytest.mark.skipif(os.name != "posix", reason="Linux kernel path semantics; exercised by image CI")
@pytest.mark.parametrize(
    "fstype,state,source,blocked",
    [
        ("nfs4", "active", "store:/recordings", False),
        ("ext4", "active", "store:/recordings", True),
        ("nfs4", "inactive", "store:/recordings", True),
        ("nfs4", "active", "wrong:/share", True),
    ],
)
def test_kernel_supervisor_mount_agreement(tmp_path, monkeypatch, fstype, state, source, blocked):
    supervisor = SimpleNamespace(
        request=lambda _: {
            "mounts": [
                dict(
                    name="frigate",
                    usage="media",
                    type="nfs",
                    state=state,
                    user_path="/media/frigate",
                    server="store",
                    path="/recordings",
                )
            ]
        }
    )
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        f"1 0 8:1 / / rw - ext4 /dev/root rw\n2 1 0:41 / /media/frigate rw - {fstype} {source} rw\n"
    )
    monkeypatch.setattr("fsm.storage.safe_path", lambda x: x)
    monkeypatch.setattr(
        "fsm.storage.filesystem_evidence",
        lambda *_: (
            list(mount_entries(mountinfo.read_text()))[-1],
            {"total": 100, "used": 60, "free": 40},
        ),
    )
    store = Storage("/media/frigate", supervisor, mountinfo)
    if blocked:
        with pytest.raises(Blocked):
            store.validate()
    else:
        assert store.validate()["free"] == 40


def test_supervisor_lifecycle_handles_lost_response_and_scopes_endpoints(monkeypatch):
    supervisor = Supervisor("not-a-real-token")
    calls = []
    state = {"value": "started"}

    def request(path, *, action=False):
        calls.append((path, action))
        if action:
            state["value"] = "stopped"
            raise Blocked("response lost")
        return {"state": state["value"]}

    monkeypatch.setattr(supervisor, "request", request)
    supervisor.lifecycle(SLUG, "stop")
    assert calls == [(f"/addons/{SLUG}/stop", True), (f"/addons/{SLUG}/info", False)]
    with pytest.raises(Blocked):
        supervisor.lifecycle("../core", "stop")
    with pytest.raises(Blocked):
        supervisor.lifecycle(SLUG, "delete")


def test_supervisor_timeout_does_not_claim_stopped(monkeypatch):
    supervisor = Supervisor("fixture")
    monkeypatch.setattr(supervisor, "request", lambda *a, **k: {"state": "started"})
    ticks = iter([0, 100])
    monkeypatch.setattr("fsm.integration.time.monotonic", lambda: next(ticks))
    with pytest.raises(Blocked, match="did not verify"):
        supervisor.lifecycle(SLUG, "stop")


def test_redirects_never_forward_supervisor_token():
    with pytest.raises(Blocked):
        NoRedirect().redirect_request(None)
