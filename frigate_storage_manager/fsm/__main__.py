import logging
import os
from pathlib import Path

from waitress import serve

from . import DESTRUCTIVE_ENABLED
from .integration import Installation, Supervisor
from .jobs import Engine, JobStore
from .safety import Blocked, read_json
from .storage import Storage
from .web import create_app


def main():
    import fcntl  # HAOS is Linux. Also excludes accidental standalone Windows use.

    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    data = Path("/data")
    # Process-wide advisory lock is released by the kernel after a crash. Never
    # delete its inode; a second process must not recover the same job concurrently.
    with (data / "process.lock").open("a") as process_lock:
        try:
            fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Blocked("Another manager process owns /data") from None
        options = read_json(data / "options.json", 65536)
        supervisor = Supervisor()
        installation = Installation(supervisor, data, options)
        storage = Storage(options.get("media_path", "/media/frigate"), supervisor)
        store = JobStore(data)
        engine = Engine(store, installation, storage, gate=lambda: DESTRUCTIVE_ENABLED)
        # Startup only reports interrupted jobs; no automatic mutations/restarts.
        app = create_app(installation, storage, store, engine)
        serve(
            app,
            host="0.0.0.0",
            port=8099,
            threads=4,
            connection_limit=24,
            channel_timeout=30,
            max_request_body_size=16384,
            clear_untrusted_proxy_headers=True,
        )


if __name__ == "__main__":
    main()
