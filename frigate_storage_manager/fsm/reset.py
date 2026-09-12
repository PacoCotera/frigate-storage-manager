"""Whole-installation reset. Bounded directory staging, streaming tree removal.

The local durable marker is the reset's point of no return. Without it, every
staged media directory and SQLite file is restored. With it, deletion is finished.
Neither a journal phase nor a missing database alone authorizes permanent removal.
"""

import os
import sqlite3
import stat
import time
from contextlib import closing

from .database import connect, validate_schema
from .jobs import TERMINAL
from .safety import Blocked, atomic_json, digest, file_identity, read_json, safe_path, sync_dir
from .storage import move_file, parent_fd, private_directory, probe_directory, unlink_file

# Frigate 0.17.2 const.py. Do not recursively erase the media share itself.
MEDIA_ROOTS = ("recordings", "clips", "exports")
RESET_PHRASE = "DELETE ALL FRIGATE DATA"


def directory_identity(path):
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        raise Blocked("Expected a Frigate media directory")
    return {"dev": info.st_dev, "ino": info.st_ino}


def move_directory(root, source, destination, identity):
    with parent_fd(root, source) as (src_fd, src), parent_fd(root, destination) as (dst_fd, dst):
        if directory_identity(safe_path(root, source)) != identity:
            raise Blocked("Reset directory identity changed")
        if safe_path(root, destination, exists=False).exists():
            raise Blocked("Reset destination already exists")
        os.rename(src, dst, src_dir_fd=src_fd, dst_dir_fd=dst_fd)
        for fd in (src_fd, dst_fd):
            if fd is not None:
                os.fsync(fd)


def tree_entries(root, rel, device, depth=0):
    """Postorder, one open iterator per level; never materialize an archive."""
    if depth > 64:
        raise Blocked("Reset directory nesting exceeds 64 levels")
    path = safe_path(root, rel)
    identity = directory_identity(path)
    if identity["dev"] != device:
        raise Blocked("Nested media mounts are not supported")
    with os.scandir(path) as entries:
        for entry in entries:
            child = rel + "/" + entry.name
            current = safe_path(root, child)
            info = current.stat(follow_symlinks=False)
            if info.st_dev != device:
                raise Blocked("Nested media mounts are not supported")
            if stat.S_ISDIR(info.st_mode):
                yield from tree_entries(root, child, device, depth + 1)
            else:
                yield "file", child, file_identity(current)
    yield "directory", rel, identity


def remove_directory(root, rel, identity):
    with parent_fd(root, rel) as (fd, name):
        if directory_identity(safe_path(root, rel)) != identity:
            raise Blocked("Reset directory identity changed before removal")
        os.rmdir(name, dir_fd=fd)
        if fd is not None:
            os.fsync(fd)


def prepare_reset(installation, storage, store, target, user):
    info, db_path, _, config_hash = installation.resolve(target)
    installation.maintenance_ready(info)
    mount = storage.validate()
    if mount["read_only"]:
        raise Blocked("NFS is read-only")
    with closing(connect(db_path)) as db:
        validate_schema(db, info["version"])
    identity = file_identity(db_path)
    document = {
        "kind": "reset",
        "target": target,
        "version": info["version"],
        "config_hash": config_hash,
        "database": {"dev": identity["dev"], "ino": identity["ino"]},
        "plan": {
            "mount": mount["identity"],
            "scope": {
                "operation": "reset",
                "cameras": [],
                "cutoff_utc": None,
                "include_exports": True,
            },
            "counts": {},
            "preserved": {},
            "bytes": 0,
            "warnings": [],
        },
    }
    token = store.preview(user, document)
    return {
        "preview_id": token,
        "confirmation": digest(document),
        "target": target,
        "media_directories": [str(storage.root / name) for name in MEDIA_ROOTS],
        "database": str(db_path),
        "expires_at": time.time() + 900,
        "phrase": RESET_PHRASE,
    }


class Reset:
    def __init__(self, engine):
        self.engine = engine
        self.store, self.installation, self.storage = (
            engine.store,
            engine.installation,
            engine.storage,
        )

    def local_folder(self, job):
        # Derive the path from the selected installation, not arbitrary journal paths.
        db = safe_path(
            self.installation.config_dir(job["target"]),
            self.installation.options.get("database_relative_path", "frigate.db"),
            exists=False,
        )
        if str(db) != job["binding"]["db"]:
            raise Blocked("Reset database path changed")
        return safe_path(db.parent, ".fsm-reset-" + job["id"], exists=False)

    def verify(self, job, *, stopped=True):
        info = self.installation.target(job["target"])
        self.installation.maintenance_ready(info)
        if stopped and info.get("state") != "stopped":
            raise Blocked("Frigate must remain stopped until reset recovery finishes")
        if (
            info["version"] != job["binding"]["version"]
            or self.installation.config_hash(job["target"]) != job["binding"]["config"]
        ):
            raise Blocked("Frigate configuration or version changed during reset")
        self.storage.validate(job["mount"])
        local = self.local_folder(job)
        db = safe_path(
            local.parent,
            self.installation.options.get("database_relative_path", "frigate.db").split("/")[-1],
            exists=False,
        )
        if stopped and db.exists():
            identity = file_identity(db)
            if {"dev": identity["dev"], "ino": identity["ino"]} != job["binding"]["database"]:
                raise Blocked("Frigate database was replaced during reset")
        return info

    def progress(self, job, count):
        if count % 128 == 0 or time.monotonic() - self.engine.last_guard >= 2:
            self.verify(job)
            self.engine.last_guard = time.monotonic()
            self.store.save(job)

    def run(self, job, document):
        try:
            info, db, _, config_hash = self.installation.resolve(job["target"])
            self.installation.maintenance_ready(info)
            identity = file_identity(db)
            if (
                document["config_hash"] != config_hash
                or document["version"] != info["version"]
                or document["database"] != {"dev": identity["dev"], "ino": identity["ino"]}
            ):
                raise Blocked("Reset target changed; review the reset again")
            self.storage.validate(document["plan"]["mount"])
            self.storage.probe()
            probe_directory(db.parent)
            job.update(
                original_running=info["state"] == "started",
                mount=document["plan"]["mount"],
                binding={
                    "db": str(db),
                    "config": config_hash,
                    "version": info["version"],
                    "database": document["database"],
                },
            )
            self.engine.phase(job, "stopping")
            if job["original_running"]:
                self.installation.supervisor.lifecycle(job["target"], "stop")
            self.verify(job)
            job.update(
                backup=f".frigate-storage-manager/{job['id']}/metadata.sqlite",
                manifest=f".frigate-storage-manager/{job['id']}/manifest.json",
            )
            self.engine.phase(job, "reset_backup")
            area = private_directory(self.storage.root / ".frigate-storage-manager")
            folder = private_directory(area / job["id"])
            private_directory(folder / "staged")
            local = private_directory(self.local_folder(job))
            backup = folder / "metadata.sqlite"
            if backup.exists():
                raise Blocked("Unexpected pre-existing reset backup")
            source, target = connect(db), sqlite3.connect(backup)
            try:
                validate_schema(source, info["version"])
                target.execute("PRAGMA journal_mode=OFF")
                deadline = time.monotonic() + 900

                def backup_progress(status, remaining, total):
                    if time.monotonic() > deadline:
                        raise Blocked("Reset backup exceeded the 15-minute budget")
                    self.progress(job, total - remaining)

                source.backup(target, pages=256, progress=backup_progress)
                target.execute("PRAGMA journal_mode=DELETE")
            finally:
                source.close()
                target.close()
            os.chmod(backup, 0o600)
            with backup.open("r+b") as saved:
                os.fsync(saved.fileno())
            with closing(connect(backup)) as check:
                validate_schema(check, info["version"])
                if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise Blocked("Reset backup failed integrity verification")
            roots = []
            for name in MEDIA_ROOTS:
                path = self.storage.file(name, exists=False)
                if path.exists():
                    roots.append({"path": name, "identity": directory_identity(path)})
            database_files = []
            for suffix in ("-wal", "-shm", "-journal", ""):
                path = safe_path(db.parent, db.name + suffix, exists=False)
                if path.exists():
                    database_files.append({"path": path.name, "identity": file_identity(path)})
            manifest = {
                "kind": "reset",
                "job": job["id"],
                "binding": job["binding"],
                "roots": roots,
                "database_files": database_files,
                "local_identity": directory_identity(local),
            }
            atomic_json(folder / "manifest.json", manifest)
            job["manifest_digest"] = digest(manifest)
            self.engine.phase(job, "reset_staging")
            for index, entry in enumerate(roots):
                self.verify(job)
                move_directory(
                    self.storage.root,
                    entry["path"],
                    self.engine.staged(job, index),
                    entry["identity"],
                )
                self.engine.hook("reset_after_media_stage")
            self.engine.phase(job, "reset_checking")
            count, size, visited = 0, 0, 0
            for index, entry in enumerate(roots):
                for kind, _, ident in tree_entries(
                    self.storage.root, self.engine.staged(job, index), entry["identity"]["dev"]
                ):
                    if kind == "file":
                        count += 1
                        size += ident["size"]
                    job.update(scanned_files=count, reset_bytes=size)
                    visited += 1
                    self.progress(job, visited)
            for index, entry in enumerate(database_files):
                self.verify(job)
                move_file(db.parent, entry["path"], f"{local.name}/{index:02d}", entry["identity"])
                self.engine.hook("reset_after_database_stage")
            # All originals are now recoverably staged on their own filesystem.
            self.verify(job)
            self.engine.phase(job, "reset_ready")
            self.engine.hook("before_reset_commit")
            atomic_json(
                local / "committed.json", {"job": job["id"], "digest": job["manifest_digest"]}
            )
            self.engine.hook("after_reset_commit")
            self.engine.phase(job, "reset_committed")
            self.recover_job(job)
        except Exception as exc:
            job["error"] = str(exc) if isinstance(exc, Blocked) else type(exc).__name__
            self.store.save(job)
            try:
                self.recover_job(job)
            except Exception as recovery_error:
                job["recovery_required"] = True
                job["recovery_error"] = (
                    str(recovery_error)
                    if isinstance(recovery_error, Blocked)
                    else type(recovery_error).__name__
                )
                self.store.save(job)

    def recover_job(self, job):
        if job["phase"] in TERMINAL:
            return
        if job["phase"] == "reserved":
            self.engine.phase(job, "rejected")
            return
        if job["phase"] == "restarting":
            self.verify(job, stopped=False)
            self.engine.finish(job, job["outcome"])
            return
        if job["phase"] == "stopping":
            info = self.verify(job, stopped=False)
            if info["state"] != "stopped":
                self.installation.supervisor.lifecycle(job["target"], "stop")
        self.verify(job)
        if job["phase"] in ("stopping", "reset_backup"):
            self.engine.finish(job, "rolled_back")
            return
        manifest = read_json(self.storage.file(job["manifest"]))
        if digest(manifest) != job["manifest_digest"] or manifest["job"] != job["id"]:
            raise Blocked("Reset manifest failed integrity verification")
        local = self.local_folder(job)
        if directory_identity(local) != manifest["local_identity"]:
            raise Blocked("Local reset staging identity changed")
        marker = safe_path(local, "committed.json", exists=False)
        committed = marker.exists()
        if committed and read_json(marker) != {"job": job["id"], "digest": job["manifest_digest"]}:
            raise Blocked("Reset commit marker does not match the manifest")
        if not committed and job["phase"] in ("reset_committed", "reset_purging"):
            raise Blocked("Reset commit marker is missing; outcome is ambiguous")
        self.engine.phase(job, "reset_purging" if committed else "reset_restoring")
        # If any live original reappeared after commit, stop before purging more.
        if committed:
            for entry in manifest["roots"]:
                if self.storage.file(entry["path"], exists=False).exists():
                    raise Blocked(
                        "Frigate media reappeared during reset; investigate before recovery"
                    )
            for entry in manifest["database_files"]:
                if safe_path(local.parent, entry["path"], exists=False).exists():
                    raise Blocked(
                        "Frigate database reappeared during reset; investigate before recovery"
                    )
        count, visited, removed = 0, 0, job.get("media_bytes_removed", 0)
        for index, entry in enumerate(manifest["roots"]):
            self.verify(job)
            source = self.storage.file(entry["path"], exists=False)
            rel = self.engine.staged(job, index)
            stage = self.storage.file(rel, exists=False)
            if stage.exists():
                if directory_identity(stage) != entry["identity"]:
                    raise Blocked("Staged reset media directory changed")
                if committed:
                    for kind, path, identity in tree_entries(
                        self.storage.root, rel, entry["identity"]["dev"]
                    ):
                        visited += 1
                        self.progress(job, visited)
                        if kind == "file":
                            unlink_file(self.storage.root, path, identity)
                            count += 1
                            removed += identity["size"]
                            job.update(processed=count, media_bytes_removed=removed)
                        else:
                            remove_directory(self.storage.root, path, identity)
                        self.engine.hook("reset_after_purge")
                else:
                    move_directory(self.storage.root, rel, entry["path"], entry["identity"])
                    self.engine.hook("reset_after_restore")
            elif not committed and (
                not source.exists() or directory_identity(source) != entry["identity"]
            ):
                raise Blocked("Uncommitted reset media is missing or changed")
        for index, entry in enumerate(manifest["database_files"]):
            rel = f"{local.name}/{index:02d}"
            staged = safe_path(local.parent, rel, exists=False)
            source = safe_path(local.parent, entry["path"], exists=False)
            if staged.exists():
                if committed:
                    unlink_file(local.parent, rel, entry["identity"])
                else:
                    move_file(local.parent, rel, entry["path"], entry["identity"])
                self.engine.hook("reset_after_database_finish")
            elif not committed and (
                not source.exists() or file_identity(source) != entry["identity"]
            ):
                raise Blocked("Uncommitted reset database is missing or changed")
        self.verify(job)
        job["media_bytes_removed"] = job.get("reset_bytes", removed) if committed else 0
        self.engine.finish(job, "completed" if committed else "rolled_back")

    def remove_local_evidence(self, job):
        local = self.local_folder(job)
        if not local.exists():
            return
        # Only known marker files and an empty directory. Never recursive removal.
        for name in ("committed.json", "committed.json.new"):
            path = safe_path(local, name, exists=False)
            if path.exists():
                unlink_file(local, name, file_identity(path))
        local.rmdir()
        sync_dir(local.parent)
