"""Local UI QA server with freshly generated synthetic files only; not in image.

Run from the repo: PYTHONPATH=frigate_storage_manager:tests python tools/ui_fixture.py
This simulates ingress solely for the disposable fake installation.
"""

import argparse
import sqlite3
import tempfile
import time
from pathlib import Path

import fsm.previews
from conftest import T, event, recording, review
from conftest import env as fixture
from fsm.safety import Blocked
from fsm.storage import StorageBlocked
from fsm.web import create_app
from waitress import serve

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--cleanup", action="store_true", help="Authorize synthetic cleanup in the UI")
parser.add_argument(
    "--supervisor-exit-error", action="store_true", help="Simulate a verified stopped/error app"
)
parser.add_argument(
    "--unsafe-boot", action="store_true", help="Show required Frigate setting changes"
)
parser.add_argument(
    "--cleanup-delay", type=float, default=0, help="Delay synthetic cleanup/reset phases"
)
parser.add_argument("--storage-error", action="store_true", help="Show synthetic mount diagnostics")
parser.add_argument(
    "--many-recordings",
    action="store_true",
    help="Generate thousands of short synthetic segments for UI review",
)
parser.add_argument(
    "--preview-error", action="store_true", help="Fail the synthetic background preview"
)
parser.add_argument(
    "--preview-delay",
    type=float,
    default=0,
    help="Delay the synthetic preview to test reconnection",
)
args = parser.parse_args()

with tempfile.TemporaryDirectory(prefix="fsm-ui-") as temp:
    data = fixture.__wrapped__(Path(temp))
    if args.cleanup:
        data.installation.options["admin_user_ids"] = ["fixture-admin"]
    if args.unsafe_boot:
        data.supervisor.app.update(boot="auto", watchdog=True, auto_update=True)
    if args.supervisor_exit_error:
        from types import SimpleNamespace

        from fsm.integration import Supervisor
        from test_supervisor_state import SupervisorTransport

        transport = SupervisorTransport(data.supervisor)
        transport.fake.app["state"], transport.running = "error", False
        data.supervisor = Supervisor("synthetic-token")
        data.supervisor.opener = SimpleNamespace(open=transport.open)
        data.installation.supervisor = data.supervisor
    if args.cleanup_delay:
        data.engine.hook = (
            lambda phase: time.sleep(args.cleanup_delay)
            if phase in ("stopped", "reset_ready")
            else None
        )
    recording(data)
    event(data)
    review(data, events=["e-old"])
    recording(data, id="kept", camera="side")
    event(data, id="bookmark", camera="side", bookmark=1)
    if args.many_recordings:
        with sqlite3.connect(data.db) as db:
            for n in range(2000):
                path = data.root / f"recordings/segment-{n}.mp4"
                path.write_bytes(b"synthetic UI fixture")
                start = T - 30000 + n * 12
                db.execute(
                    "INSERT INTO recordings(id,camera,path,start_time,end_time,duration,segment_size) VALUES (?,'front',?,?,?,10,1)",
                    (
                        f"segment-{n}",
                        f"/media/frigate/recordings/segment-{n}.mp4",
                        start,
                        start + 10,
                    ),
                )
            db.executemany(
                "INSERT INTO recordings(id,camera,path,start_time,end_time,duration,segment_size) VALUES (?,'front',?,?,?,10,1)",
                (
                    (
                        f"kept-{n}",
                        f"/media/frigate/recordings/kept-{n}.mp4",
                        T + n * 10,
                        T + n * 10 + 10,
                    )
                    for n in range(2000)
                ),
            )
        db.close()
        for n in range(80):
            event(data, id=f"recent-{n}", start=T + n, end=T + n + 1)
    if args.storage_error:

        def unavailable(*args, **kwargs):
            raise StorageBlocked(
                "This app sees ext4 at /media/frigate; NFS access is not confirmed. See Validation details. Cleanup remains blocked.",
                {
                    "media_path": "/media/frigate",
                    "opened_mount": {"fstype": "ext4", "source": "/dev/fixture"},
                    "supervisor_nfs_mounts": [
                        {
                            "name": "frigate",
                            "state": "active",
                            "server": "fixture",
                            "path": "/media",
                        }
                    ],
                },
            )

        data.storage.validate = unavailable
    app = create_app(data.installation, data.storage, data.store, data.engine)
    if args.preview_delay or args.preview_error:
        original_plan = fsm.previews.build_plan

        def delayed_plan(*values, **options):
            options["progress"]("checking_storage")
            time.sleep(args.preview_delay)
            if args.preview_error:
                raise Blocked("Synthetic preview failure: storage changed after validation.")
            return original_plan(*values, **options)

        fsm.previews.build_plan = delayed_plan

    def ingress(environ, start_response):
        environ["REMOTE_ADDR"] = "172.30.32.2"
        environ["HTTP_X_REMOTE_USER_ID"] = "fixture-admin"
        return app(environ, start_response)

    print("Synthetic-only UI: http://127.0.0.1:8765", flush=True)
    serve(ingress, host="127.0.0.1", port=8765, threads=4)
