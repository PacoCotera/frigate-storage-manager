"""Exact 0.17.2 schema contract, generated from upstream migrations 001-032."""

import json
import re
import sqlite3
import time
from pathlib import Path

from .safety import Blocked, safe_path

SCHEMA = json.loads(Path(__file__).with_name("schema-0.17.2.json").read_text())
VECTORS = {"vec_thumbnails": "thumbnail_embedding", "vec_descriptions": "description_embedding"}
MARKER_SQL = "CREATE TABLE _fsm_commit (job TEXT PRIMARY KEY, digest TEXT NOT NULL)"


def supported_version(version):
    if not isinstance(version, str) or not re.fullmatch(
        r"0\.17\.2(?:[-+][A-Za-z0-9._-]+)?", version
    ):
        raise Blocked("Only Frigate 0.17.2 (including build suffixes) is supported")


def connect(path, *, writable=False, seconds=120):
    safe_path(path.parent, path.name)
    db = sqlite3.connect(
        path.as_uri() + ("?mode=rw" if writable else "?mode=ro"),
        uri=True,
        timeout=5,
        isolation_level=None,
    )
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA cache_size=-4096")
    db.execute("PRAGMA temp_store=FILE")
    db.execute("PRAGMA foreign_keys=ON")
    if writable:
        db.execute("PRAGMA synchronous=FULL")
    deadline = time.monotonic() + seconds
    db.set_progress_handler(lambda: time.monotonic() > deadline, 10000)
    return db


def load_vectors(db):
    import sqlite_vec

    db.enable_load_extension(True)
    try:
        sqlite_vec.load(db)
    finally:
        db.enable_load_extension(False)
    if db.execute("SELECT vec_version()").fetchone()[0] != "v0.1.3":
        raise Blocked("The verified sqlite-vec 0.1.3 extension is required")


def validate_schema(db, version):
    supported_version(version)
    entries = list(db.execute("SELECT type,name,sql FROM sqlite_master"))
    tables = {r["name"]: r["sql"] for r in entries if r["type"] == "table"}
    if any(r["type"] in ("trigger", "view") for r in entries):
        raise Blocked("Unreviewed database triggers or views are present")
    for name, expected in SCHEMA.items():
        if name not in tables:
            raise Blocked(f"Unsupported schema: missing {name}")
        actual = [list(r) for r in db.execute(f'PRAGMA table_info("{name}")')]
        foreign = [list(r) for r in db.execute(f'PRAGMA foreign_key_list("{name}")')]
        if actual != expected["columns"] or foreign != expected["foreign_keys"]:
            raise Blocked(f"Unsupported schema or relationships in {name}")
    vec = set(tables) & VECTORS.keys()
    if vec and vec != VECTORS.keys():
        raise Blocked("Incomplete semantic vector schema")
    allowed = set(SCHEMA) | {"sqlite_sequence"}
    if "_fsm_commit" in tables:
        if tables["_fsm_commit"] != MARKER_SQL:
            raise Blocked("Unknown maintenance commit marker schema")
        allowed.add("_fsm_commit")
    if vec:
        load_vectors(db)
        for name, embedding in VECTORS.items():
            normalized = re.sub(r"\s+", "", tables[name]).lower().replace('"', "")
            expected = f"createvirtualtable{name}usingvec0(idtextprimarykey,{embedding}float[768]distance_metric=cosine)"
            if normalized != expected:
                raise Blocked("Unreviewed semantic vector definition")
        # 0.1.3 does not label its internal tables as 'shadow'. Reproduce their
        # exact definitions with the pinned extension instead of trusting prefixes.
        reference = sqlite3.connect(":memory:")
        try:
            load_vectors(reference)
            for name in vec:
                reference.execute(tables[name])
            for name, sql in reference.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='table'"
            ):
                if tables.get(name) != sql:
                    raise Blocked("Semantic vector backing tables differ from sqlite-vec 0.1.3")
                allowed.add(name)
        finally:
            reference.close()
    if set(tables) - allowed:
        raise Blocked("Unreviewed database tables: " + ", ".join(sorted(set(tables) - allowed)))
    if db.execute("PRAGMA foreign_key_check").fetchone():
        raise Blocked("Existing foreign-key violations require investigation")
    return sorted(vec)
