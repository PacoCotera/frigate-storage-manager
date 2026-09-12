"""Local UI QA server with freshly generated synthetic files only; not in image.

Run from the repo: PYTHONPATH=frigate_storage_manager:tests python tools/ui_fixture.py
This simulates ingress solely for the disposable fake installation, with deletion locked.
"""

import tempfile
from pathlib import Path

from conftest import env as fixture
from conftest import event, recording, review
from fsm.web import create_app
from waitress import serve

with tempfile.TemporaryDirectory(prefix="fsm-ui-") as temp:
    data = fixture.__wrapped__(Path(temp))
    recording(data)
    event(data)
    review(data, events=["e-old"])
    recording(data, id="kept", camera="side")
    event(data, id="bookmark", camera="side", bookmark=1)
    app = create_app(data.installation, data.storage, data.store, data.engine)

    def ingress(environ, start_response):
        environ["REMOTE_ADDR"] = "172.30.32.2"
        environ["HTTP_X_REMOTE_USER_ID"] = "fixture-admin"
        return app(environ, start_response)

    print("Synthetic-only UI: http://127.0.0.1:8765", flush=True)
    serve(ingress, host="127.0.0.1", port=8765, threads=4)
