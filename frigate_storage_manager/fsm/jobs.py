"""Durable jobs and recovery across independent SQLite/NFS operations.

The commit witness is written IN the Frigate transaction. Journal phases alone
never decide whether a lost COMMIT response succeeded. Uncertainty leaves the
selected app stopped; no finally block blindly restarts it.
"""

import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from .database import MARKER_SQL, VECTORS, connect, validate_schema
from .planner import build_plan, expanded, row_signature, summary
from .safety import Blocked, atomic_json, digest, file_identity, read_json, safe_path, sync_dir
from .storage import move_file, private_directory, probe_directory, unlink_file

TERMINAL = {"completed", "rolled_back", "needs_preview", "rejected"}
STAGED_PHASES = {"prepared", "staging", "committing", "committed", "purging", "restarting"}


class NeedsPreview(Blocked):
    pass


class JobStore:
    def __init__(self, data):
        self.root = Path(data)
        self.root.mkdir(mode=0o700, exist_ok=True)
        safe_path(self.root)
        os.chmod(self.root, 0o700)
        self.path = self.root / "state.sqlite"
        with self.connect() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS previews
                (id TEXT PRIMARY KEY, user TEXT, created REAL, document TEXT);
                CREATE TABLE IF NOT EXISTS jobs
                (id TEXT PRIMARY KEY, updated REAL, document TEXT);""")

    def connect(self):
        safe_path(self.root, self.path.name, exists=False)
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def preview(self, user, document):
        import json

        token = secrets.token_hex(24)
        raw = json.dumps(document, allow_nan=False)
        if len(raw) > 16 * 1024 * 1024:
            raise Blocked("Preview exceeds its 16 MiB state limit")
        with self.connect() as db:
            db.execute("DELETE FROM previews WHERE created < ?", (time.time() - 900,))
            db.execute(
                "DELETE FROM previews WHERE id NOT IN (SELECT id FROM previews ORDER BY created DESC LIMIT 3)"
            )
            db.execute("INSERT INTO previews VALUES (?,?,?,?)", (token, user, time.time(), raw))
        return token

    def reserve(self, token, user, confirmation):
        import json

        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if any(j["phase"] not in TERMINAL for j in self.jobs(db)):
                raise Blocked("Another job is active or requires recovery")
            backups = [j for j in self.jobs(db) if j.get("backup") and not j.get("backup_removed")]
            if len(backups) >= 5:
                raise Blocked(
                    "Five metadata backups retained; remove a completed backup before another job"
                )
            row = db.execute(
                "SELECT user,created,document FROM previews WHERE id=?", (token,)
            ).fetchone()
            if not row or row[0] != user or row[1] < time.time() - 900:
                raise Blocked("Preview expired, was already used, or belongs to another user")
            document = json.loads(row[2])
            if confirmation != digest(document):
                raise Blocked("Confirmation does not match the frozen preview")
            job = {
                "id": token,
                "phase": "reserved",
                "target": document["target"],
                "created": time.time(),
                "summary": summary(document["plan"]),
            }
            db.execute("INSERT INTO jobs VALUES (?,?,?)", (token, time.time(), json.dumps(job)))
            db.execute("DELETE FROM previews WHERE id=?", (token,))
        return job, document

    def save(self, job):
        import json

        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET updated=?,document=? WHERE id=?",
                (time.time(), json.dumps(job, allow_nan=False), job["id"]),
            )

    def jobs(self, db=None):
        import json

        if db is not None:
            return [
                json.loads(r[0])
                for r in db.execute("SELECT document FROM jobs ORDER BY updated DESC")
            ]
        with self.connect() as connection:
            return self.jobs(connection)

    def get(self, token):
        for job in self.jobs():
            if job["id"] == token:
                return job
        raise Blocked("Unknown job")


class Engine:
    def __init__(self, store, installation, storage, gate=lambda: False, hook=lambda phase: None):
        self.store, self.installation, self.storage = store, installation, storage
        self.gate, self.hook = gate, hook
        self.lock = threading.Lock()

    def phase(self, job, phase):
        job["phase"] = phase
        self.store.save(job)
        self.hook(phase)

    def submit(self, token, user, confirmation, *, background=True):
        if not self.gate():
            raise Blocked(
                "Deletion is disabled in 0.1.0 pending live HAOS validation and a reviewed release"
            )
        if not self.lock.acquire(blocking=False):
            raise Blocked("A maintenance worker is already active")
        try:
            job, document = self.store.reserve(token, user, confirmation)
        except BaseException:
            self.lock.release()
            raise

        def worker():
            try:
                self.run(job, document)
            finally:
                self.lock.release()

        if background:
            threading.Thread(target=worker, name="cleanup", daemon=False).start()
        else:
            worker()
        return self.store.get(job["id"])

    def verify(self, job, *, stopped=True):
        info, db, config, config_hash = self.installation.resolve(job["target"], offline=True)
        self.installation.maintenance_ready(info)
        if stopped and info.get("state") != "stopped":
            raise Blocked(
                "Frigate is not stopped; resolve external lifecycle interference before recovery"
            )
        if job.get("binding"):
            identity = file_identity(db)
            binding = {
                "db": str(db),
                "dev": identity["dev"],
                "ino": identity["ino"],
                "config": config_hash,
                "version": info["version"],
            }
            if binding != job["binding"]:
                raise Blocked(
                    "Database, configuration, or target version changed during maintenance"
                )
        self.storage.validate(job.get("mount"))
        return info, db, config

    def run(self, job, document):
        try:
            info, db_path, config, config_hash = self.installation.resolve(job["target"])
            self.installation.maintenance_ready(info)
            if info.get("state") not in ("started", "stopped"):
                raise Blocked("Frigate must be in a stable started or stopped state")
            if document["config_hash"] != config_hash or document["version"] != info["version"]:
                raise NeedsPreview("Target configuration/version changed; create a fresh preview")
            identity = file_identity(db_path)
            if document["database"] != {"dev": identity["dev"], "ino": identity["ino"]}:
                raise NeedsPreview("Database identity changed; create a fresh preview")
            self.storage.validate(document["plan"]["mount"])
            self.storage.probe()
            probe_directory(db_path.parent)
            job.update(
                original_running=info["state"] == "started",
                mount=document["plan"]["mount"],
                binding={
                    "db": str(db_path),
                    "dev": identity["dev"],
                    "ino": identity["ino"],
                    "config": config_hash,
                    "version": info["version"],
                },
            )
            self.phase(job, "stopping")  # Persist original state BEFORE calling Supervisor.
            if job["original_running"]:
                self.installation.supervisor.lifecycle(job["target"], "stop")
            self.verify(job)
            self.phase(job, "stopped")
            scope = document["plan"]["scope"]
            current = build_plan(
                db_path,
                info["version"],
                self.storage,
                config,
                scope["cameras"],
                scope["cutoff_utc"],
                scope["include_exports"],
                self.installation.options.get("max_plan_items", 10000),
            )
            if expanded(document["plan"], current):
                raise NeedsPreview(
                    "Offline selection expands or changes the approved scope; preview again"
                )
            job["summary"] = summary(current)
            if not any(current["rows"].values()):
                self.finish(job, "completed")
                return
            job.update(
                backup=f".frigate-storage-manager/{job['id']}/metadata.sqlite",
                manifest=f".frigate-storage-manager/{job['id']}/manifest.json",
            )
            self.phase(job, "backup")
            area = private_directory(self.storage.root / ".frigate-storage-manager")
            folder = private_directory(area / job["id"])
            private_directory(folder / "staged")
            backup = folder / "metadata.sqlite"
            if backup.exists():
                raise Blocked("Unexpected pre-existing metadata backup")
            source = connect(db_path)
            target = sqlite3.connect(backup)
            try:
                target.execute("PRAGMA journal_mode=OFF")
                source.backup(target, pages=256)
            finally:
                source.close()
                target.close()
            os.chmod(backup, 0o600)
            with backup.open("r+b") as saved:
                os.fsync(saved.fileno())
            check = connect(backup)
            try:
                validate_schema(check, info["version"])
                if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise Blocked("Metadata backup failed integrity verification")
            finally:
                check.close()
            manifest = {
                "job": job["id"],
                "target": job["target"],
                "binding": job["binding"],
                "original_running": job["original_running"],
                "plan": current,
            }
            atomic_json(folder / "manifest.json", manifest)
            job.update(
                backup=backup.relative_to(self.storage.root).as_posix(),
                manifest=(folder / "manifest.json").relative_to(self.storage.root).as_posix(),
                manifest_digest=digest(manifest),
                processed=0,
            )
            self.phase(job, "prepared")
            self.phase(job, "staging")
            for index, file in enumerate(current["files"]):
                self.verify(job)
                stage = self.staged(job, index)
                self.hook("before_stage")
                move_file(self.storage.root, file["path"], stage, file["identity"])
                self.hook("after_stage")
                job["processed"] = index + 1
                self.store.save(job)
            self.verify(job)
            self.phase(job, "committing")
            self.commit(job, current, db_path)
            self.phase(job, "committed")
            self.recover_job(job)
        except Exception as exc:
            # Exceptions contain only our generic error text at the API boundary.
            job["error"] = str(exc) if isinstance(exc, Blocked) else type(exc).__name__
            self.store.save(job)
            try:
                self.recover_job(
                    job,
                    terminal="needs_preview" if isinstance(exc, NeedsPreview) else "rolled_back",
                )
            except Exception as recovery_error:
                job["recovery_required"] = True
                job["recovery_error"] = (
                    str(recovery_error)
                    if isinstance(recovery_error, Blocked)
                    else type(recovery_error).__name__
                )
                self.store.save(job)

    def staged(self, job, index):
        return f".frigate-storage-manager/{job['id']}/staged/{index:08d}"

    def commit(self, job, plan, db_path):
        db = connect(db_path, writable=True)
        try:
            validate_schema(db, job["binding"]["version"])
            db.execute("BEGIN IMMEDIATE")
            # Check the exact selected row content, including related metadata.
            for table, rows in plan["rows"].items():
                key = "rowid" if table == "timeline" else "id"
                for entry in rows:
                    if table in VECTORS:
                        row = db.execute(
                            f'SELECT id,{VECTORS[table]} FROM "{table}" WHERE id=?', (entry["id"],)
                        ).fetchone()
                    else:
                        row = db.execute(
                            f'SELECT {key} AS _key,* FROM "{table}" WHERE {key}=?', (entry["id"],)
                        ).fetchone()
                    if row is None or row_signature(row) != entry["signature"]:
                        raise Blocked("Database changed after offline selection")
            # Known children first; leave users, triggers/configuration and models.
            order = [
                *plan["vectors"],
                "timeline",
                "userreviewstatus",
                "event",
                "reviewsegment",
                "recordings",
                "previews",
                "export",
            ]
            for table in order:
                key = "rowid" if table == "timeline" else "id"
                for entry in plan["rows"][table]:
                    db.execute(f'DELETE FROM "{table}" WHERE {key}=?', (entry["id"],))
                    self.hook("database_update")
            if db.execute("PRAGMA foreign_key_check").fetchone():
                raise Blocked("Cleanup would leave broken foreign-key relationships")
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='_fsm_commit'").fetchone():
                db.execute(MARKER_SQL)
            db.execute("DELETE FROM _fsm_commit")  # Only one active job, bounded witness storage.
            db.execute("INSERT INTO _fsm_commit VALUES (?,?)", (job["id"], job["manifest_digest"]))
            self.verify(job)
            self.hook("before_commit")
            db.commit()
            self.hook("after_commit")  # Includes simulated ambiguous successful COMMIT responses.
        finally:
            db.close()  # An uncommitted transaction rolls back before recovery probes.

    def committed(self, job, db_path):
        db = connect(db_path)
        try:
            validate_schema(db, job["binding"]["version"])
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='_fsm_commit'").fetchone():
                return False
            row = db.execute("SELECT digest FROM _fsm_commit WHERE job=?", (job["id"],)).fetchone()
            if row and row[0] != job["manifest_digest"]:
                raise Blocked("Database commit witness differs from durable manifest")
            return row is not None
        finally:
            db.close()

    def recover(self, token):
        if not self.gate():
            raise Blocked("Recovery mutations are disabled in this validation release")
        if not self.lock.acquire(blocking=False):
            raise Blocked("Wait for the active worker before recovering")
        try:
            job = self.store.get(token)
            if job["phase"] in TERMINAL:
                raise Blocked("This job is already resolved")
            try:
                self.recover_job(job)
            except Exception as exc:
                job["recovery_required"] = True
                job["recovery_error"] = str(exc) if isinstance(exc, Blocked) else type(exc).__name__
                self.store.save(job)
                raise
            return self.store.get(token)
        finally:
            self.lock.release()

    def recover_job(self, job, terminal="rolled_back"):
        if job["phase"] in TERMINAL:
            return
        if job["phase"] == "reserved":
            self.phase(job, "rejected")  # No lifecycle/file mutation was possible yet.
            return
        # The response to a stop may have been lost. No file mutation precedes
        # stopped verification. Safely finish stopping before any recovery work.
        if job["phase"] == "stopping":
            self.verify(job, stopped=False)
            if self.installation.target(job["target"]).get("state") != "stopped":
                self.installation.supervisor.lifecycle(job["target"], "stop")
        if job["phase"] == "restarting":
            self.verify(job, stopped=False)
            self.finish(job, job["outcome"])
            return
        _, db_path, _ = self.verify(job)
        if job["phase"] in ("stopping", "stopped", "backup"):
            # No staging has begun. Partial backup artifacts remain visible.
            self.finish(job, terminal)
            return
        manifest = read_json(self.storage.file(job["manifest"]))
        if digest(manifest) != job["manifest_digest"] or manifest["job"] != job["id"]:
            raise Blocked("Recovery manifest failed integrity verification")
        committed = self.committed(job, db_path)
        if job["phase"] in ("committed", "purging") and not committed:
            raise Blocked(
                "Journal reports a commit but its database witness is missing; outcome is ambiguous"
            )
        files = manifest["plan"]["files"]
        self.verify_outcome(job, manifest["plan"], db_path, committed)
        self.phase(job, "purging" if committed else "staging")
        indices = range(len(files)) if committed else reversed(range(len(files)))
        for index in indices:
            self.verify(job)
            file = files[index]
            source = self.storage.file(file["path"], exists=False)
            stage = self.storage.file(self.staged(job, index), exists=False)
            if committed:
                if source.exists():
                    raise Blocked("Committed cleanup source unexpectedly exists; refusing to guess")
                if stage.exists():
                    unlink_file(self.storage.root, self.staged(job, index), file["identity"])
                    self.hook("after_purge")
            else:
                if source.exists() and stage.exists():
                    raise Blocked("Both staged and original media exist; recovery is ambiguous")
                if stage.exists():
                    move_file(
                        self.storage.root, self.staged(job, index), file["path"], file["identity"]
                    )
                elif not source.exists() or file_identity(source) != file["identity"]:
                    raise Blocked(
                        "Uncommitted media is missing or changed; recovery needs investigation"
                    )
                self.hook("after_restore")
        self.verify(job)
        self.verify_outcome(job, manifest["plan"], db_path, committed)
        job["media_bytes_removed"] = manifest["plan"]["bytes"] if committed else 0
        self.finish(job, "completed" if committed else terminal)

    def verify_outcome(self, job, plan, db_path, committed):
        """A missing witness is only rollback evidence when all originals survive."""
        db = connect(db_path)
        try:
            validate_schema(db, job["binding"]["version"])
            for table, rows in plan["rows"].items():
                key = "rowid" if table == "timeline" else "id"
                for entry in rows:
                    columns = f"id,{VECTORS[table]}" if table in VECTORS else f"{key} AS _key,*"
                    row = db.execute(
                        f'SELECT {columns} FROM "{table}" WHERE {key}=?', (entry["id"],)
                    ).fetchone()
                    if committed and row is not None:
                        raise Blocked(
                            "Commit witness exists but selected metadata remains; outcome is ambiguous"
                        )
                    if not committed and (row is None or row_signature(row) != entry["signature"]):
                        raise Blocked(
                            "Commit witness absent and original metadata differs; outcome is ambiguous"
                        )
        finally:
            db.close()

    def finish(self, job, outcome):
        job["outcome"] = outcome
        self.phase(job, "restarting")
        if job.get("original_running"):
            self.installation.supervisor.lifecycle(job["target"], "start")
        elif self.installation.target(job["target"]).get("state") != "stopped":
            raise Blocked("Originally stopped Frigate changed state unexpectedly")
        job["recovery_required"] = False
        job["completed_at"] = time.time()
        self.phase(job, outcome)

    def remove_backup(self, token):
        if not self.gate():
            raise Blocked("Backup removal is disabled in the validation release")
        if not self.lock.acquire(blocking=False):
            raise Blocked("A worker is active")
        try:
            job = self.store.get(token)
            if any(j["phase"] not in TERMINAL for j in self.store.jobs()):
                raise Blocked("Resolve interrupted jobs before removing backups")
            if job["phase"] not in TERMINAL or not job.get("backup") or job.get("backup_removed"):
                raise Blocked("No completed backup is available")
            self.storage.validate(job["mount"])
            for key in ("backup", "manifest"):
                path = self.storage.file(job[key], exists=False)
                if path.exists():
                    unlink_file(self.storage.root, job[key], file_identity(path))
            staged = self.storage.file(f".frigate-storage-manager/{token}/staged", exists=False)
            if staged.exists():
                staged.rmdir()  # Only succeeds if empty; never recursive deletion.
            if staged.parent.exists():
                staged.parent.rmdir()
                sync_dir(staged.parent.parent)
            job["backup_removed"] = True
            self.store.save(job)
            return job
        finally:
            self.lock.release()
