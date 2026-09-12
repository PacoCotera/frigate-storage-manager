import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import approve, ids, recording
from crash_worker import engine_for
from test_planner import plan


@pytest.mark.parametrize(
    "phase,committed",
    [
        ("staging", False),
        ("after_stage", False),
        ("database_update", False),
        ("before_commit", False),
        ("after_commit", True),
        ("after_purge", True),
        ("restarting", True),
    ],
)
def test_actual_process_exit_with_live_wal_transaction(env, phase, committed):
    recording(env)
    with sqlite3.connect(env.db) as db:
        db.execute("PRAGMA journal_mode=WAL")
    root = env.data.parent
    (root / "supervisor-state.json").write_text(json.dumps({"state": "started"}))
    token, signature = approve(env, plan(env))
    repo = Path(__file__).resolve().parents[1]
    process = subprocess.run(
        [sys.executable, str(repo / "tests/crash_worker.py"), str(root), phase, token, signature],
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
    restarted = engine_for(root)
    result = restarted.recover(token)
    assert result["phase"] == ("completed" if committed else "rolled_back"), result
    assert ids(env, "recordings") == ([] if committed else ["r-old"])
    assert (env.root / "recordings/r-old.mp4").exists() is not committed
    assert json.loads((root / "supervisor-state.json").read_text())["state"] == "started"
