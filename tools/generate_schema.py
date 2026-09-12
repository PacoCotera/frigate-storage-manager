"""Reproduce the schema from *all* pinned upstream migrations (development only).

Usage: python tools/generate_schema.py /path/to/frigate-v0.17.2
No Frigate services or heavyweight Frigate dependencies are imported.
"""

import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import types
from pathlib import Path

from peewee import SqliteDatabase
from peewee_migrate import Router

PIN = "3d4dd3ac4b00e7257bd3412608a783001d7d77ed"
source = Path(sys.argv[1]).resolve()
assert (
    subprocess.check_output(
        [
            "git",
            "-c",
            f"safe.directory={source.as_posix()}",
            "-C",
            str(source),
            "rev-parse",
            "HEAD",
        ],
        text=True,
    ).strip()
    == PIN
)
module = types.ModuleType("frigate")
module.__path__ = [str(source / "frigate")]
sys.modules["frigate"] = module
spec = importlib.util.spec_from_file_location("frigate.models", source / "frigate/models.py")
models = importlib.util.module_from_spec(spec)
sys.modules["frigate.models"] = models
spec.loader.exec_module(models)
with tempfile.TemporaryDirectory() as temp:
    db = SqliteDatabase(str(Path(temp) / "schema.db"))
    Router(db, migrate_dir=source / "migrations").run()
    db.close()
    con = sqlite3.connect(str(Path(temp) / "schema.db"))
    rows = con.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL "
        "AND name NOT LIKE 'sqlite_%' ORDER BY type DESC,name"
    ).fetchall()
    schema = {}
    for kind, name, sql in rows:
        if kind == "table":
            schema[name] = {
                "columns": [list(x) for x in con.execute(f'PRAGMA table_info("{name}")')],
                "foreign_keys": [
                    list(x) for x in con.execute(f'PRAGMA foreign_key_list("{name}")')
                ],
            }
    repo = Path(__file__).resolve().parents[1]
    fixtures = repo / "tests/fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    (fixtures / "frigate-0.17.2.sql").write_text(
        f"-- Generated from Frigate {PIN}, migrations 001-032.\n"
        + "\n".join(sql + ";" for _, _, sql in rows),
        encoding="utf-8",
    )
    (repo / "frigate_storage_manager/fsm/schema-0.17.2.json").write_text(
        json.dumps(schema, indent=2) + "\n", encoding="utf-8"
    )
    con.close()
