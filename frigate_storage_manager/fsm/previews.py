"""One bounded read-only preview worker, durable status, and owner-bound results."""

import json
import logging
import re
import secrets
import threading
import time

from .jobs import TERMINAL
from .planner import build_plan, summary
from .safety import Blocked, cutoff_utc, digest, file_identity, identifier
from .storage import StorageBlocked

ACTIVE = ("queued", "running")
LOG = logging.getLogger("fsm.preview")


class PreviewService:
    def __init__(self, installation, storage, store):
        self.installation, self.storage, self.store = installation, storage, store
        self.lock = threading.Lock()
        self.inspection_lock = threading.Lock()
        self.thread = None
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS preview_tasks
                (id TEXT PRIMARY KEY, user TEXT, created REAL, updated REAL, state TEXT, document TEXT)""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_active_preview
                ON preview_tasks((1)) WHERE state IN ('queued','running')""")
            # A prior process's read-only worker cannot survive an app restart.
            for row in db.execute(
                "SELECT id,document FROM preview_tasks WHERE state IN ('queued','running')"
            ):
                document = json.loads(row[1])
                document.update(
                    phase="interrupted",
                    error="App restarted before this preview finished. Create a new preview.",
                )
                db.execute(
                    "UPDATE preview_tasks SET state='interrupted',updated=?,document=? WHERE id=?",
                    (time.time(), json.dumps(document), row[0]),
                )
                LOG.warning("Preview %s interrupted by app restart", row[0])

    def _row(self, db, token, user):
        row = db.execute(
            "SELECT id,user,created,updated,state,document FROM preview_tasks WHERE id=? AND user=?",
            (token, user),
        ).fetchone()
        if not row:
            raise Blocked("Preview unavailable or belongs to another user")
        return row

    def _public(self, row, db):
        document = json.loads(row[5])
        public = {"id": row[0], "created": row[2], "updated": row[3], "state": row[4], **document}
        public["elapsed_seconds"] = round(
            max(0, (time.time() if row[4] in ACTIVE else row[3]) - row[2]), 1
        )
        public["expired"] = False
        if row[4] == "completed":
            exists = db.execute(
                "SELECT 1 FROM previews WHERE id=? AND user=? AND created>=?",
                (document["preview_id"], row[1], time.time() - 900),
            ).fetchone()
            public["expired"] = exists is None
        return public

    def get(self, token, user):
        with self.store.connect() as db:
            return self._public(self._row(db, token, user), db)

    def recent(self, user):
        with self.store.connect() as db:
            rows = list(
                db.execute(
                    "SELECT id,user,created,updated,state,document FROM preview_tasks WHERE user=? ORDER BY created DESC LIMIT 4",
                    (user,),
                )
            )
            return [self._public(r, db) for r in rows]

    def busy(self):
        with self.store.connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM preview_tasks WHERE state IN ('queued','running')"
                ).fetchone()
                is not None
            )

    def submit(self, body, user):
        if not isinstance(body, dict):
            raise Blocked("Invalid preview request")
        token = body.get("request_id") or secrets.token_hex(24)
        if not isinstance(token, str) or not re.fullmatch(r"[a-fA-F0-9-]{32,48}", token):
            raise Blocked("Invalid preview request ID")
        with self.lock, self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM preview_tasks WHERE id=?", (token,)).fetchone():
                return self._public(self._row(db, token, user), db)
            active = db.execute(
                "SELECT id,user,created,updated,state,document FROM preview_tasks WHERE state IN ('queued','running')"
            ).fetchone()
            if active:
                if active[1] == user:
                    return self._public(active, db)
                raise Blocked("Another user has a preview running. Try again when it finishes.")
            if any(j["phase"] not in TERMINAL for j in self.store.jobs(db)):
                raise Blocked("Resolve the active cleanup/recovery job before a preview")
            target = identifier(body.get("target"))
            cameras = body.get("cameras")
            if not isinstance(cameras, list) or not 1 <= len(cameras) <= 256:
                raise Blocked("Select at least one camera")
            cameras = sorted(identifier(c) for c in cameras)
            if len(set(cameras)) != len(cameras):
                raise Blocked("Duplicate camera selection")
            cutoff_utc(body.get("cutoff"))
            exports = body.get("include_exports", False)
            if type(exports) is not bool:
                raise Blocked("Invalid export policy")
            request = {
                "target": target,
                "cameras": cameras,
                "cutoff": body["cutoff"],
                "include_exports": exports,
            }
            document = {"request": request, "phase": "queued", "processed": None, "total": None}
            now = time.time()
            # Four global task receipts and at most four existing bounded plans.
            db.execute(
                "DELETE FROM preview_tasks WHERE id NOT IN (SELECT id FROM preview_tasks ORDER BY created DESC LIMIT 3)"
            )
            db.execute(
                "INSERT INTO preview_tasks VALUES (?,?,?,?,?,?)",
                (token, user, now, now, "queued", json.dumps(document)),
            )
            db.commit()  # Publish the task before any worker or browser reads it.
            self.thread = threading.Thread(
                target=self._run, args=(token, user, request), name="preview", daemon=True
            )
            try:
                self.thread.start()
            except Exception:
                document.update(phase="failed", error="Could not start the preview worker. Retry.")
                db.execute(
                    "UPDATE preview_tasks SET state='failed',document=? WHERE id=?",
                    (json.dumps(document), token),
                )
                db.commit()
                raise Blocked("Could not start the preview worker. Retry.") from None
            return self._public(self._row(db, token, user), db)

    def _save(self, token, state, document):
        with self.store.connect() as db:
            db.execute(
                "UPDATE preview_tasks SET state=?,updated=?,document=? WHERE id=?",
                (state, time.time(), json.dumps(document), token),
            )

    def _run(self, token, user, request):
        started = time.monotonic()
        document = {"request": request, "timings": {}}
        last = {"phase": None, "saved": 0, "started": started}

        def finish_phase():
            if last["phase"]:
                document["timings"][last["phase"]] = round(time.monotonic() - last["started"], 3)

        def progress(phase, **values):
            changed = phase != last["phase"]
            if not changed and time.monotonic() - last["saved"] < 2:
                return
            if changed:
                finish_phase()
                last["started"] = time.monotonic()
            document.update(
                phase=phase,
                processed=values.get("processed"),
                total=values.get("total"),
                category=values.get("category"),
            )
            self._save(token, "running", document)
            if changed:
                LOG.info(
                    "Preview %s phase=%s elapsed=%.1fs", token, phase, time.monotonic() - started
                )
            last.update(phase=phase, saved=time.monotonic())

        try:
            progress("connecting")
            info, db, config, fingerprint = self.installation.resolve(request["target"])
            identity = file_identity(db)
            plan = build_plan(
                db,
                info["version"],
                self.storage,
                config,
                request["cameras"],
                request["cutoff"],
                request["include_exports"],
                self.installation.options.get("max_plan_items", 10000),
                inspect=True,
                progress=progress,
            )
            progress("saving_result")
            frozen = {
                "target": request["target"],
                "version": info["version"],
                "config_hash": fingerprint,
                "database": {"dev": identity["dev"], "ino": identity["ino"]},
                "plan": plan,
            }
            preview_id = self.store.preview(user, frozen)
            finish_phase()
            document.update(
                phase="completed",
                result=summary(plan) | {"overview": plan["inspection"]["overview"]},
                preview_id=preview_id,
                confirmation=digest(frozen),
                expires_at=time.time() + 900,
                processed=None,
                total=None,
            )
            self._save(token, "completed", document)
            LOG.info("Preview %s completed elapsed=%.1fs", token, time.monotonic() - started)
        except Exception as exc:
            finish_phase()
            document.update(
                phase="failed",
                failed_phase=last["phase"],
                processed=None,
                total=None,
                error=str(exc)
                if isinstance(exc, Blocked)
                else "Preview failed; check manager logs for its phase and error type.",
            )
            if isinstance(exc, StorageBlocked):
                document["diagnostics"] = exc.diagnostics
            self._save(token, "failed", document)
            # No paths, camera/user identifiers, API bodies or configuration in logs.
            LOG.warning(
                "Preview %s failed phase=%s error_type=%s elapsed=%.1fs",
                token,
                last["phase"],
                type(exc).__name__,
                time.monotonic() - started,
            )

    def items(self, token, user, view, kind, search, page):
        # Decode only one bounded plan at a time across the HTTP threads.
        with self.inspection_lock:
            return self._items(token, user, view, kind, search, page)

    def _items(self, token, user, view, kind, search, page):
        if view not in ("selected", "preserved") or len(search) > 128 or page < 0 or page > 1000:
            raise Blocked("Invalid preview inspection filter")
        inspection = self._inspection(token, user)
        entries = inspection[view]
        query = search.casefold().strip()
        matches = [
            entry
            for entry in entries
            if (not kind or entry["kind"] == kind)
            and (
                not query
                or query
                in " ".join(
                    str(entry.get(k, "")) for k in ("id", "camera", "label", "reason")
                ).casefold()
            )
        ]
        return {
            "items": matches[page * 50 : (page + 1) * 50],
            "page": page,
            "page_size": 50,
            "matched": len(matches),
            "retained": len(entries),
            "preserved_samples": inspection["preserved_samples"],
            "preserved_limit_per_category": inspection["preserved_limit_per_category"],
        }

    def hours(self, token, user, camera, page):
        if page < 0 or page > 2000:
            raise Blocked("Invalid time-window page")
        with self.inspection_lock:
            inspection = self._inspection(token, user)
            if "hours" not in inspection:
                raise Blocked("This older preview has no grouped summary; create a new preview")
            entries = [entry for entry in inspection["hours"] if entry["camera"] == camera]
            return {
                "items": entries[page * 12 : (page + 1) * 12],
                "matched": len(entries),
                "page": page,
                "page_size": 12,
            }

    def _inspection(self, token, user):
        with self.store.connect() as db:
            task = self._public(self._row(db, token, user), db)
            if task["state"] != "completed" or task["expired"]:
                raise Blocked("Preview is not complete or has expired; create a fresh preview")
            row = db.execute(
                "SELECT document FROM previews WHERE id=? AND user=? AND created>=?",
                (task["preview_id"], user, time.time() - 900),
            ).fetchone()
            if not row:
                raise Blocked("Preview expired; create a fresh preview")
        return json.loads(row[0])["plan"]["inspection"]
