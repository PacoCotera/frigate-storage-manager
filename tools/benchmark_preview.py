"""Synthetic preview benchmark; no HAOS connection or real media is used."""

import json
import sqlite3
import tempfile
import time
from pathlib import Path

from conftest import CUTOFF, SLUG, T, env, event, recording
from fsm.planner import build_plan
from fsm.previews import PreviewService

with tempfile.TemporaryDirectory(prefix="fsm-benchmark-") as temp:
    data = env.__wrapped__(Path(temp))
    recording(data)
    # Separate media files; never hardlink or reuse a file across selected rows.
    with sqlite3.connect(data.db) as db:
        template = db.execute("SELECT * FROM recordings").fetchone()
        for n in range(2000):
            path = data.root / f"recordings/selected-{n}.mp4"
            path.write_bytes(b"synthetic benchmark")
            values = list(template)
            values[0] = f"selected-{n}"
            # Use column names rather than assuming migration column ordering.
            columns = [r[1] for r in db.execute("PRAGMA table_info(recordings)")]
            values[columns.index("path")] = f"/media/frigate/recordings/selected-{n}.mp4"
            db.execute(f"INSERT INTO recordings VALUES ({','.join('?' for _ in values)})", values)
        db.executemany(
            "INSERT INTO recordings(id,camera,path,start_time,end_time,duration,segment_size) VALUES (?,'front',?,?,?,10,1)",
            (
                (f"archive-{n}", f"/media/frigate/recordings/archive-{n}.mp4", T + n, T + n + 10)
                for n in range(25000)
            ),
        )
    db.close()
    for n in range(80):
        event(data, id=f"kept-{n}", start=T + n, end=T + n + 1)
    for inspect in (False, True):
        started = time.monotonic()
        phases = {}

        def progress(phase, **values):
            phases.setdefault(phase, round(time.monotonic() - started, 3))

        result = build_plan(
            data.db,
            data.version,
            data.storage,
            data.config,
            ["front"],
            CUTOFF,
            inspect=inspect,
            progress=progress,
        )
        print(
            json.dumps(
                {
                    "inspect": inspect,
                    "seconds": round(time.monotonic() - started, 3),
                    "phase_start_seconds": phases,
                    "selected_recordings": result["counts"]["recordings"],
                }
            ),
            flush=True,
        )
    service = PreviewService(data.installation, data.storage, data.store)
    started = time.monotonic()
    task = service.submit({"target": SLUG, "cameras": ["front"], "cutoff": CUTOFF}, "benchmark")
    service.thread.join(150)
    task = service.get(task["id"], "benchmark")
    print(
        json.dumps(
            {
                "worker_seconds": round(time.monotonic() - started, 3),
                "state": task["state"],
                "error": task.get("error"),
            }
        ),
        flush=True,
    )
