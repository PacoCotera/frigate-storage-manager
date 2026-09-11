"""Small, fail-closed primitives shared by preview and recovery."""

import hashlib
import json
import math
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


class Blocked(Exception):
    """An actionable validation failure, safe to show to an ingress user."""


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise Blocked("Invalid app or camera identifier")
    return value


def relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise Blocked("Unsafe relative path")
    parts = value.split("/")
    if any(p in ("", ".", "..") or any(ord(c) < 32 for c in p) for p in parts):
        raise Blocked("Unsafe relative path")
    return PurePosixPath(value)


def safe_path(root, rel="", *, exists=True):
    """Reject symlinks/reparse points in every component, including the root.

    Maintenance additionally uses directory-fd operations on Linux. This check also
    works in the Windows synthetic suite; it never normalizes away a traversal.
    """
    root = Path(root).absolute()
    path = root.joinpath(*relative(rel).parts) if rel else root
    for part in [*reversed(path.parents), path]:
        try:
            s = part.lstat()
        except FileNotFoundError:
            if exists:
                raise Blocked("Required path is missing") from None
            continue
        if stat.S_ISLNK(s.st_mode) or getattr(s, "st_file_attributes", 0) & 0x400:
            raise Blocked("Symlinks and reparse points are not supported")
    if not path.is_relative_to(root):
        raise Blocked("Path escapes its selected root")
    return path


def file_identity(path):
    s = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
        raise Blocked("Expected a regular file with one link")
    return {"dev": s.st_dev, "ino": s.st_ino, "size": s.st_size, "mtime": s.st_mtime_ns}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def sync_dir(path):
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_json(path, value):
    path = Path(path)
    safe_path(path.parent)
    temp = safe_path(path.parent, path.name + ".new", exists=False)
    # A leftover .new can only be our own interrupted write; do not follow links.
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(value, out, sort_keys=True, separators=(",", ":"), allow_nan=False)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)
    sync_dir(path.parent)


def read_json(path, limit=32 * 1024 * 1024):
    if path.stat().st_size > limit:
        raise Blocked("State file exceeds the supported size")
    with path.open(encoding="utf-8") as src:
        return json.load(src)


def cutoff_utc(value, now=None):
    if not isinstance(value, str):
        raise Blocked("Cutoff must include a UTC offset")
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError()
        ts = date.timestamp()
    except (ValueError, OverflowError):
        raise Blocked("Use an ISO date and time with an explicit UTC offset") from None
    now = now or datetime.now(timezone.utc).timestamp()
    if not math.isfinite(ts) or not 0 < ts < now:
        raise Blocked("Cutoff must be in the past")
    return ts
