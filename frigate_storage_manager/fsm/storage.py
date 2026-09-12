"""NFS evidence, capacity and narrow file operations; no library walks."""

import contextlib
import os
import re
import secrets
import stat
from pathlib import Path, PurePosixPath

from .safety import Blocked, file_identity, relative, safe_path, sync_dir


class StorageBlocked(Blocked):
    """Storage failure with explicitly selected, credential-free evidence."""

    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.diagnostics = diagnostics


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


def descriptor_mount_id(fd):
    info = Path(f"/proc/self/fdinfo/{fd}").read_text()
    match = re.search(r"^mnt_id:\s*(\d+)\s*$", info, re.MULTILINE)
    if not match:
        raise Blocked("Linux did not provide the opened media directory's mount ID")
    return match[1]


def filesystem_evidence(root, mountinfo):
    # Opening the directory activates an existing HAOS automount. lstat alone
    # does not. Read mountinfo afterwards, and select the descriptor's mount ID:
    # an autofs trigger and its NFS mount can have the same mountpoint, and a
    # longer matching path can even belong to a hidden mount tree.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(root, flags)
    try:
        mount_id = descriptor_mount_id(fd)
        matches = [m for m in mount_entries(mountinfo.read_text()) if m["mount_id"] == mount_id]
        if len(matches) != 1 or not root.is_relative_to(Path(matches[0]["mountpoint"])):
            raise Blocked(
                "Opened media mount is missing from this app's mount table; retry validation"
            )
        mount = matches[0]
        opened = os.fstat(fd)
        if mount["device"] != f"{os.major(opened.st_dev)}:{os.minor(opened.st_dev)}":
            raise Blocked("Opened media device differs from its kernel mount entry")
        capacity = os.fstatvfs(fd)
        # A detach/overmount during the probe must not validate a stale descriptor.
        current = os.open(root, flags)
        try:
            if descriptor_mount_id(current) != mount_id:
                raise Blocked(
                    "Media mount changed during validation; retry after storage stabilizes"
                )
        finally:
            os.close(current)
        return mount, {
            "total": capacity.f_blocks * capacity.f_frsize,
            "used": (capacity.f_blocks - capacity.f_bfree) * capacity.f_frsize,
            "free": capacity.f_bavail * capacity.f_frsize,
        }
    finally:
        os.close(fd)


class Storage:
    def __init__(self, root, supervisor, mountinfo=Path("/proc/self/mountinfo")):
        self.root, self.supervisor, self.mountinfo = Path(root), supervisor, mountinfo
        if not self.root.is_absolute() or not self.root.is_relative_to(Path("/media")):
            raise Blocked("Media path must be beneath the existing /media mapping")
        relative(str(self.root.relative_to("/media")))

    def validate(self, expected=None):
        diagnostics = {"media_path": str(self.root)}
        try:
            return self._validate(expected, diagnostics)
        except Blocked as exc:
            raise StorageBlocked(str(exc), diagnostics) from None
        except OSError as exc:
            diagnostics["os_error"] = exc.errno
            raise StorageBlocked(
                "This app could not access its media mount; see Validation details", diagnostics
            ) from None

    def _validate(self, expected, diagnostics):
        configured_mounts = [
            m
            for m in self.supervisor.request("/mounts").get("mounts", [])
            if m.get("usage") == "media" and m.get("type") == "nfs"
        ]
        diagnostics["supervisor_nfs_mounts"] = [
            {k: m.get(k) for k in ("name", "state", "user_path", "server", "path", "read_only")}
            for m in configured_mounts
        ]
        safe_path(self.root)
        mount, capacity = filesystem_evidence(self.root, self.mountinfo)
        diagnostics["opened_mount"] = {
            k: mount[k] for k in ("mount_id", "device", "root", "mountpoint", "fstype", "source")
        }
        if mount["fstype"] not in ("nfs", "nfs4"):
            raise Blocked(
                f"This app sees {mount['fstype']} at {self.root}; NFS access is not confirmed. "
                "See Validation details. Cleanup remains blocked."
            )
        matches = [
            m
            for m in configured_mounts
            if m.get("user_path") and self.root.is_relative_to(Path(m["user_path"]))
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
        readonly = "ro" in (mount["options"] + "," + mount["super_options"]).split(",")
        return {
            "identity": ident,
            **capacity,
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
    renamed = name + "-renamed"
    fd = os.open(
        path,
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0),
        0o600,
    )
    owned = os.fstat(fd)
    try:
        try:
            os.write(fd, b"fsm-access-probe\n")
            os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            if os.read(fd, 64) != b"fsm-access-probe\n":
                raise Blocked("Disposable write probe did not read back correctly")
        finally:
            os.close(fd)
        identity = file_identity(path)
        if (identity["dev"], identity["ino"]) != (owned.st_dev, owned.st_ino):
            raise Blocked("Disposable probe was replaced")
        move_file(root, name, renamed, identity)
        if safe_path(root, renamed).read_bytes() != b"fsm-access-probe\n":
            raise Blocked("Renamed disposable probe did not read back correctly")
    finally:
        # Only our exclusive-create file can be removed, even if rename/fsync
        # failed after the directory entry moved. Never touch existing media.
        for rel in (name, renamed):
            candidate = safe_path(root, rel, exists=False)
            if candidate.exists():
                identity = file_identity(candidate)
                if (identity["dev"], identity["ino"]) != (owned.st_dev, owned.st_ino):
                    raise Blocked("Disposable probe path changed; refusing to remove it")
                unlink_file(root, rel, identity)
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
