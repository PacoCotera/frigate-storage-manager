"""Exercise real Supervisor HTTP parsing and its use by maintenance workers."""

import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

import pytest
from conftest import SLUG, approve, ids, recording
from fsm.integration import Supervisor
from fsm.safety import Blocked
from test_jobs import Crash, crash_at, fresh_engine
from test_planner import plan
from test_reset import reset_fixture, reset_preview, submit_reset
from test_security_storage import client


class SupervisorTransport:
    """Synthetic API; state/error responses match Supervisor 2026.09.0."""

    def __init__(self, fake):
        self.fake = fake
        self.running = True
        self.calls = []
        self.stats_failure = None
        self.after_stats = None
        self.stop_fails = False
        self.lost_stop_response = False

    def open(self, request, timeout):
        path = urlsplit(request.full_url).path
        self.calls.append((request.method, path))
        if path == "/addons":
            result = {"addons": [self.fake.app.copy()]}
        elif path == "/addons/self/info":
            result = {"hassio_api": True, "hassio_role": "manager"}
        elif path == f"/addons/{SLUG}/info":
            result = self.fake.app.copy()
        elif path == f"/addons/{SLUG}/stats":
            assert urlsplit(request.full_url).query == "one_shot=true"
            if self.stats_failure:
                raise self.stats_failure
            if not self.running:
                if self.after_stats:
                    self.fake.app["state"] = self.after_stats
                raise self.failure()
            result = {"cpu_percent": 0}  # Idle is still running.
        elif path == f"/addons/{SLUG}/stop":
            assert request.method == "POST"
            self.fake.app["state"] = "error"
            if self.stop_fails:
                raise self.failure(key="app_unknown_error")
            self.running = False
            if self.lost_stop_response:
                raise URLError("Stop response lost after shutdown")
            result = {}
        elif path == f"/addons/{SLUG}/start":
            assert request.method == "POST"
            self.fake.app["state"], self.running = "started", True
            result = {}
        else:
            result = self.fake.request(path, frigate=SLUG)
            if path == "/api/version":
                return io.BytesIO(result.encode())
            return io.BytesIO(json.dumps(result).encode())
        return io.BytesIO(json.dumps({"result": "ok", "data": result}).encode())

    @staticmethod
    def failure(status=400, key="app_not_running_error", slug=SLUG, body=None):
        if body is None:
            body = json.dumps(
                {"result": "error", "error_key": key, "extra_fields": {"app": slug}}
            ).encode()
        return HTTPError("http://supervisor/synthetic", status, "synthetic", {}, io.BytesIO(body))


@pytest.fixture
def transport(env):
    api = SupervisorTransport(env.supervisor)
    supervisor = Supervisor("synthetic-token")
    supervisor.opener = SimpleNamespace(open=api.open)
    env.supervisor = supervisor
    env.installation.supervisor = supervisor
    return api


def test_error_state_requires_target_bound_not_running_evidence(env, transport):
    transport.fake.app["state"], transport.running = "error", False
    c, headers = client(env)
    result = c.post("/api/validate", json={"target": SLUG}, headers=headers)
    assert result.status_code == 200
    assert result.json["state"] == "stopped"
    assert result.json["reported_state"] == "error"
    assert result.json["stop_verified"] is True
    assert result.json["maintenance"]["ready"]
    assert all(method == "GET" for method, _ in transport.calls)


@pytest.mark.parametrize(
    "failure",
    [
        URLError("unavailable"),
        TimeoutError(),
        SupervisorTransport.failure(status=401),
        SupervisorTransport.failure(status=500),
        SupervisorTransport.failure(slug="another_app"),
        SupervisorTransport.failure(key="app_stats_timeout_error"),
        SupervisorTransport.failure(body=b"App is not running"),
        SupervisorTransport.failure(body=b'{"result":"error","message":"App is not running"}'),
        SupervisorTransport.failure(body=b"[]"),
        SupervisorTransport.failure(body=b'{"extra_fields": []}'),
        SupervisorTransport.failure(body=b"x" * 8193),
    ],
)
def test_errors_and_untrusted_shapes_never_unlock_cleanup(env, transport, failure):
    transport.fake.app["state"] = "error"
    transport.stats_failure = failure
    info = env.supervisor.info(SLUG)
    assert info["state"] == "error"
    assert not info.get("stop_verified")
    assert "has not confirmed it stopped" in " ".join(env.installation.maintenance_blockers(info))


def test_error_with_idle_but_running_container_stays_blocked(env, transport):
    transport.fake.app["state"] = "error"
    info = env.supervisor.info(SLUG)
    assert info["state"] == "error"
    with pytest.raises(Blocked):
        env.installation.maintenance_ready(info)


@pytest.mark.parametrize("state", ["started", "startup", "unknown"])
def test_concurrent_start_or_unknown_state_is_not_hidden(env, transport, state):
    transport.fake.app["state"], transport.running = "error", False
    transport.after_stats = state
    info = env.supervisor.info(SLUG)
    assert info["state"] == state
    assert not info.get("stop_verified")


def test_stop_evidence_is_never_cached(env, transport):
    transport.fake.app["state"], transport.running = "error", False
    assert env.supervisor.info(SLUG)["stop_verified"]
    transport.running = True
    assert env.supervisor.info(SLUG)["state"] == "error"


def test_lost_stop_response_accepts_only_fresh_not_running_evidence(env, transport):
    transport.lost_stop_response = True
    env.supervisor.lifecycle(SLUG, "stop")
    assert transport.fake.app["state"] == "error"
    assert not transport.running
    assert env.supervisor.info(SLUG)["stop_verified"]


@pytest.mark.parametrize("state", [None, "unknown", "startup"])
def test_unknown_and_transitional_states_are_not_treated_as_stopped(env, transport, state):
    transport.fake.app["state"], transport.running = state, False
    info = env.supervisor.info(SLUG)
    assert info["state"] == state
    assert env.installation.maintenance_blockers(info)
    assert not any(path.endswith("/stats") for _, path in transport.calls)


@pytest.mark.parametrize("kind", ["cleanup", "reset"])
@pytest.mark.parametrize("running", [True, False])
def test_cleanup_and_reset_accept_verified_exit_error_and_keep_original_state(
    env, transport, kind, running
):
    reset_fixture(env)
    transport.running = running
    transport.fake.app["state"] = "started" if running else "error"
    if kind == "reset":
        job = submit_reset(env)
        assert not env.db.exists()
    else:
        token, confirmation = approve(env, plan(env))
        job = env.engine.submit(token, "admin", confirmation, background=False)
        assert "r-old" in ids(env, "recordings")  # Required by the bookmarked event.
    assert job["phase"] == "completed", job
    assert job["original_running"] is running
    assert transport.running is running
    assert env.supervisor.info(SLUG)["state"] == ("started" if running else "stopped")


@pytest.mark.parametrize("kind", ["cleanup", "reset"])
@pytest.mark.parametrize("committed", [True, False])
def test_recovery_rechecks_verified_exit_error_without_restarting_stopped_app(
    env, transport, kind, committed
):
    recording(env)
    transport.fake.app["state"], transport.running = "error", False
    if kind == "reset":
        approved = reset_preview(env)
        token = approved["preview_id"]
        env.engine.hook = crash_at("reset_committed" if committed else "reset_ready")
        with pytest.raises(Crash):
            submit_reset(env, approved)
    else:
        token, confirmation = approve(env, plan(env))
        env.engine.hook = crash_at("committed" if committed else "prepared")
        with pytest.raises(Crash):
            env.engine.submit(token, "admin", confirmation, background=False)
    result = fresh_engine(env).recover(token)
    assert result["phase"] == ("completed" if committed else "rolled_back"), result
    assert (env.root / "recordings/r-old.mp4").exists() is not committed
    assert not transport.running
    assert all(method == "GET" for method, _ in transport.calls)


def test_failed_stop_with_live_stats_cannot_stage_or_delete(env, transport, monkeypatch):
    recording(env)
    token, confirmation = approve(env, plan(env))
    transport.stop_fails = True
    clock = iter([0, 1, 100])
    monkeypatch.setattr("fsm.integration.time.monotonic", lambda: next(clock))
    monkeypatch.setattr("fsm.integration.time.sleep", lambda _: None)
    job = env.engine.submit(token, "admin", confirmation, background=False)
    assert job["recovery_required"]
    assert job["phase"] == "stopping"
    assert (env.root / "recordings/r-old.mp4").exists()
    assert ids(env, "recordings") == ["r-old"]
    assert transport.running


def test_start_still_requires_started_even_with_verified_exit_error(env, transport, monkeypatch):
    transport.fake.app["state"], transport.running = "error", False
    original = env.supervisor.request

    def request(path, **kwargs):
        if path.endswith("/start"):
            raise Blocked("Start failed")
        return original(path, **kwargs)

    monkeypatch.setattr(env.supervisor, "request", request)
    clock = iter([0, 1, 100])
    monkeypatch.setattr("fsm.integration.time.monotonic", lambda: next(clock))
    monkeypatch.setattr("fsm.integration.time.sleep", lambda _: None)
    with pytest.raises(Blocked, match="did not verify Frigate started"):
        env.supervisor.lifecycle(SLUG, "start")
