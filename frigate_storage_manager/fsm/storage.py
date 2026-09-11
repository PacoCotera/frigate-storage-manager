"""NFS evidence, capacity and narrow file operations; no library walks."""

import contextlib
import os
import re
import secrets
import shutil
import stat
from pathlib import Path, PurePosixPath

from .safety import Blocked, file_identity, relative, safe_path, sync_dir


def mount_entries(text):
    def unescape(value):
        return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), value)

    for line in text.splitlines():
        left, right = line.split(" - ", 1)
        fields, tail = left.split(), right.split()
        yield {
            "mount_id": fields[0],
            "device": fields[2],
            "root": unescape(fields[3]),
            "mountpoint": unescape(fields[4]),
            "options": fields[5],
            "fstype": tail[0],
            "source": unescape(tail[1]),
            "super_options": tail[2],
        }


class Storage:
    def __init__(self, root, supervisor, mountinfo=Path("/proc/self/mountinfo")):
        self.root, self.supervisor, self.mountinfo = Path(root), supervisor, mountinfo
        if not self.root.is_absolute() or not self.root.is_relative_to(Path("/media")):
            raise Blocked("Media path must be beneath the existing /media mapping")
        relative(str(self.root.relative_to("/media")))

    def validate(self, expected=None):
        safe_path(self.root)
        candidates = [
            m
            for m in mount_entries(self.mountinfo.read_text())
            if self.root.is_relative_to(Path(m["mountpoint"]))
        ]
        if not candidates:
            raise Blocked("No kernel mount covers the selected media directory")
        mount = max(candidates, key=lambda m: len(m["mountpoint"]))
        if mount["fstype"] not in ("nfs", "nfs4"):
            raise Blocked(
                "Media is not on a mounted NFS filesystem; refusing a local fallback directory"
            )
        matches = [
            m
            for m in self.supervisor.request("/mounts").get("mounts", [])
            if m.get("usage") == "media"
            and m.get("type") == "nfs"
            and m.get("user_path")
            and self.root.is_relative_to(Path(m["user_path"]))
        ]
        if len(matches) != 1 or matches[0].get("state") != "active":
            raise Blocked("Supervisor does not confirm exactly one active NFS media mount")
        configured = matches[0]
        source = f"{configured.get('server')}:{configured.get('path')}"
        if mount["source"].rstrip("/") != source.rstrip("/"):
            raise Blocked("Kernel NFS source differs from Supervisor's configured share")
        ident = {k: mount[k] for k in ("device", "root", "mountpoint", "fstype", "source")}
        ident.update(name=configured["name"], path=str(self.root))
        # A reboot may change mount ID/device. Never silently weaken identity;
        # recovery then needs explicit operator investigation.
        if expected is not None and ident != expected:
            raise Blocked("Media mount identity changed; recovery needs investigation")
        capacity = shutil.disk_usage(self.root)
        readonly = "ro" in (mount["options"] + "," + mount["super_options"]).split(",")
        return {
            "identity": ident,
            "total": capacity.total,
            "used": capacity.used,
            "free": capacity.free,
            "read_only": readonly,
            "available": True,
        }

    def file(self, rel, *, exists=True):
        path = safe_path(self.root, rel, exists=exists)
        if path.exists() and path.stat().st_dev != self.root.stat().st_dev:
            raise Blocked("Nested media mounts are not supported")
        return path

    def media_relative(self, value, kind):
        path = PurePosixPath(value)
        if not path.is_absolute() or "\\" in value or ".." in path.parts:
            raise Blocked("Indexed media path is unsafe")
        try:
            rel = str(path.relative_to(PurePosixPath("/media/frigate")))
        except ValueError:
            raise Blocked("Indexed file is outside Frigate's media root") from None
        # A custom HAOS media root is an explicit remapping of Frigate's fixed
        # /media/frigate container root, never an arbitrary database path.
        prefixes = {
            "recordings": "recordings/",
            "previews": "clips/previews/",
            "review_thumbnails": "clips/review/",
            "exports": "exports/",
            "export_thumbnails": "clips/export/",
        }
        if not rel.startswith(prefixes[kind]):
            raise Blocked("Indexed path is outside its expected media category")
        relative(rel)
        return rel

    def probe(self):
        evidence = self.validate()
        if evidence["read_only"]:
            raise Blocked("NFS is read-only")
        probe_directory(self.root)
        return evidence


def probe_directory(root):
    root = safe_path(root)
    name = ".fsm-probe-" + secrets.token_hex(16)
    path = root / name
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, b"fsm-access-probe\n")
        os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        if os.read(fd, 64) != b"fsm-access-probe\n":
            raise Blocked("Disposable write probe did not read back correctly")
    finally:
        os.close(fd)
        path.unlink()
        sync_dir(root)


def private_directory(path):
    safe_path(path.parent)
    path.mkdir(mode=0o700, exist_ok=True)
    safe_path(path)
    if os.name == "posix":
        info = path.stat()
        if stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.geteuid():
            raise Blocked("Recovery directory must be owned by this app with mode 0700")
    sync_dir(path.parent)
    return path


@contextlib.contextmanager
def parent_fd(root, rel):
    parts = relative(rel).parts
    safe_path(root, rel, exists=False)
    if os.name != "posix":  # Synthetic Windows tests; HAOS always uses anchored descriptors.
        yield None, str(Path(root).joinpath(*parts))
        return
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd, parts[-1]
    finally:
        os.close(fd)


def move_file(root, source, destination, identity):
    with parent_fd(root, source) as (src_fd, src), parent_fd(root, destination) as (dst_fd, dst):
        path = safe_path(root, source)
        if file_identity(path) != identity:
            raise Blocked("Media file changed since offline selection")
        try:
            os.stat(dst, dir_fd=dst_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise Blocked("Staging destination already exists")
        os.rename(src, dst, src_dir_fd=src_fd, dst_dir_fd=dst_fd)
        for fd in (src_fd, dst_fd):
            if fd is not None:
                os.fsync(fd)


def unlink_file(root, rel, identity):
    with parent_fd(root, rel) as (fd, name):
        if file_identity(safe_path(root, rel)) != identity:
            raise Blocked("Staged media identity changed")
        os.unlink(name, dir_fd=fd)
        if fd is not None:
            os.fsync(fd)
