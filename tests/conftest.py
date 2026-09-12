import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fsm.integration import Installation
from fsm.jobs import Engine, JobStore
from fsm.safety import Blocked, digest, file_identity
from fsm.storage import Storage, probe_directory

SLUG = "ccab4aaf_frigate"
VERSION = "0.17.2-3d4dd3a"
CUTOFF = "2026-01-02T00:00:00+00:00"
T = 1767312000.0


class FakeSupervisor:
    def __init__(self):
        self.calls = []
        self.app = dict(
            slug=SLUG,
            name="Frigate",
            version=VERSION,
            state="started",
            boot="manual",
            watchdog=False,
            auto_update=False,
        )
        self.config = {
            "database": {"path": "/config/frigate.db"},
            "record": {},
            "cameras": {"front": {"record": {}}, "side": {"record": {}}},
        }
        self.fail_action = None

    def discover(self):
        return [self.app.copy()]

    def info(self, slug):
        if slug == "self":
            return {"hassio_api": True, "hassio_role": "manager"}
        assert slug == SLUG
        return self.app.copy()

    def request(self, path, *, frigate=None, action=False):
        self.calls.append((path, action, frigate))
        if path == "/api/version":
            return VERSION
        if path == "/api/config":
            return self.config
        raise AssertionError(path)

    def lifecycle(self, slug, action):
        assert slug == SLUG
        self.calls.append((action, slug))
        if self.fail_action == action:
            raise Blocked("Supervisor lifecycle failed")
        self.app["state"] = "stopped" if action == "stop" else "started"


class FakeStorage(Storage):
    """Only kernel/Supervisor mount evidence is faked; actual file IO is tested."""

    def __init__(self, root):
        self.root = root
        self.unavailable = False
        self.readonly = False
        self.identity = {"device": "synthetic-nfs", "source": "fixture:/media", "path": str(root)}

    def validate(self, expected=None):
        if self.unavailable:
            raise Blocked("NFS unavailable")
        if expected is not None and expected != self.identity:
            raise Blocked("Media mount identity changed")
        return {
            "identity": self.identity.copy(),
            "total": 10000000,
            "used": 1000000,
            "free": 9000000,
            "available": True,
            "read_only": self.readonly,
        }

    def probe(self):
        value = self.validate()
        if self.readonly:
            raise Blocked("NFS read-only")
        probe_directory(self.root)
        return value


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    configs = tmp_path / "configs"
    target = configs / SLUG
    target.mkdir(parents=True)
    (target / "config.yml").write_text("# Synthetic fixture only\ncameras: {}\n")
    path = target / "frigate.db"
    db = sqlite3.connect(path)
    db.executescript(
        Path(__file__).with_name("fixtures").joinpath("frigate-0.17.2.sql").read_text()
    )
    db.close()
    supervisor = FakeSupervisor()
    data = tmp_path / "data"
    data.mkdir()
    options = {
        "database_relative_path": "frigate.db",
        "target_slug": SLUG,
        "admin_user_ids": ["admin"],
        "max_plan_items": 10000,
    }
    installation = Installation(supervisor, data, options, configs)
    storage = FakeStorage(root)
    store = JobStore(data)
    engine = Engine(store, installation, storage, gate=lambda: True)
    info, _, config, config_hash = installation.resolve(SLUG)
    return SimpleNamespace(
        root=root,
        data=data,
        db=path,
        supervisor=supervisor,
        installation=installation,
        storage=storage,
        store=store,
        engine=engine,
        config=config,
        config_hash=config_hash,
        version=info["version"],
    )


def insert(env, table, **values):
    db = sqlite3.connect(env.db)
    row = {}
    for _, name, kind, required, default, primary in db.execute(f'PRAGMA table_info("{table}")'):
        if name in values:
            row[name] = values[name]
        elif name == "id" and "INT" in kind:
            continue
        elif name == "camera":
            row[name] = "front"
        elif name in ("start_time", "timestamp", "date"):
            row[name] = T - 100
        elif name == "end_time":
            row[name] = T - 90
        elif name == "thumbnail":
            row[name] = "aW5saW5l"
        elif name in ("has_clip",):
            row[name] = 1
        elif required and default is None:
            row[name] = (
                "{}"
                if kind == "JSON"
                else (0 if kind in ("REAL", "INTEGER", "DATETIME") else "fixture")
            )
    row.update(values)
    columns = ",".join(f'"{k}"' for k in row)
    placeholders = ",".join("?" for _ in row)
    cur = db.execute(
        f'INSERT INTO "{table}" ({columns}) VALUES ({placeholders})', tuple(row.values())
    )
    db.commit()
    last = cur.lastrowid
    db.close()
    return last


def media(env, rel, content=b"synthetic media"):
    path = env.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return "/media/frigate/" + rel


def recording(env, id="r-old", start=T - 100, end=T - 90, camera="front"):
    return insert(
        env,
        "recordings",
        id=id,
        camera=camera,
        start_time=start,
        end_time=end,
        path=media(env, f"recordings/{id}.mp4"),
    )


def event(env, id="e-old", start=T - 100, end=T - 90, camera="front", bookmark=0, **kwargs):
    return insert(
        env,
        "event",
        id=id,
        start_time=start,
        end_time=end,
        camera=camera,
        retain_indefinitely=bookmark,
        **kwargs,
    )


def review(env, id="review-old", events=None, start=T - 100, end=T - 90, camera="front"):
    return insert(
        env,
        "reviewsegment",
        id=id,
        camera=camera,
        start_time=start,
        end_time=end,
        data=json.dumps({"detections": events or []}),
        severity="alert",
        thumb_path=media(env, f"clips/review/thumb-{id}.webp"),
    )


def approve(env, plan, user="admin"):
    identity = file_identity(env.db)
    doc = {
        "target": SLUG,
        "version": env.version,
        "config_hash": env.config_hash,
        "database": {"dev": identity["dev"], "ino": identity["ino"]},
        "plan": plan,
    }
    token = env.store.preview(user, doc)
    return token, digest(doc)


def ids(env, table):
    with sqlite3.connect(env.db) as db:
        return [r[0] for r in db.execute(f'SELECT id FROM "{table}" ORDER BY id')]
