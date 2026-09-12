"""Local UI QA server with freshly generated synthetic files only; not in image.

Run from the repo: PYTHONPATH=frigate_storage_manager:tests python tools/ui_fixture.py
This simulates ingress solely for the disposable fake installation, with deletion locked.
"""

import argparse
import tempfile
import time
from pathlib import Path

import fsm.previews
from conftest import env as fixture
from conftest import event, recording, review
from fsm.safety import Blocked
from fsm.storage import StorageBlocked
from fsm.web import create_app
from waitress import serve

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--storage-error", action="store_true", help="Show synthetic mount diagnostics")
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
    recording(data)
    event(data)
    review(data, events=["e-old"])
    recording(data, id="kept", camera="side")
    event(data, id="bookmark", camera="side", bookmark=1)
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
