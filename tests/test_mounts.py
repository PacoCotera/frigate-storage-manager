"""Kernel mount selection, including HAOS 2026.09 automount stacks."""

import errno
import os
from pathlib import Path
from types import SimpleNamespace

import fsm.storage as storage_module
import pytest
from conftest import SLUG
from fsm.safety import Blocked
from fsm.storage import Storage, StorageBlocked, filesystem_evidence
from test_security_storage import client


def kernel(tmp_path, monkeypatch, *, ids=("7", "7"), device="0:41", failure=None):
    root = tmp_path / "media" / "frigate"
    table = tmp_path / "mountinfo"
    calls = []
    opened_ids = iter(ids)
    fake = SimpleNamespace(
        O_RDONLY=0,
        O_DIRECTORY=65536,
        O_NOFOLLOW=131072,
        fstat=lambda _: SimpleNamespace(st_dev=41),
        major=lambda _: int(device.split(":")[0]),
        minor=lambda _: int(device.split(":")[1]),
        fstatvfs=lambda _: SimpleNamespace(f_blocks=100, f_bfree=40, f_bavail=35, f_frsize=4096),
        close=lambda fd: calls.append(("close", fd)),
    )

    def open_directory(path, flags):
        assert path == root
        assert flags == fake.O_RDONLY | fake.O_DIRECTORY | fake.O_NOFOLLOW
        calls.append(("open", 80 + len(calls)))
        if failure:
            raise failure
        return calls[-1][1]

    fake.open = open_directory
    monkeypatch.setattr(storage_module, "os", fake)
    monkeypatch.setattr(storage_module, "descriptor_mount_id", lambda _: next(opened_ids))
    return root, table, calls, fake


def line(root, number="7", parent="6", fs="nfs4", device="0:41", source="store:/recordings"):
    point = root.as_posix().replace(" ", r"\040")
    return f"{number} {parent} {device} / {point} rw - {fs} {source} rw\n"


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("hidden_fs", ["autofs", "ext4"])
def test_opened_nfs_wins_over_hidden_mount_at_same_path(tmp_path, monkeypatch, reverse, hidden_fs):
    root, table, calls, _ = kernel(tmp_path, monkeypatch)
    rows = [line(root, "6", "1", hidden_fs, "0:40"), line(root)]
    table.write_text("".join(reversed(rows) if reverse else rows))
    mount, capacity = filesystem_evidence(root, table)
    assert mount["fstype"] == "nfs4" and mount["mount_id"] == "7"
    assert capacity == {"total": 409600, "used": 245760, "free": 143360}
    assert [kind for kind, _ in calls] == ["open", "open", "close", "close"]


def test_open_activates_automount_before_reading_mountinfo(tmp_path, monkeypatch):
    root, table, _, fake = kernel(tmp_path, monkeypatch)
    dormant = line(root, "6", "1", "autofs", "0:40")
    table.write_text(dormant)
    original_open = fake.open

    def activate(path, flags):
        table.write_text(dormant + line(root))
        return original_open(path, flags)

    fake.open = activate
    assert filesystem_evidence(root, table)[0]["mount_id"] == "7"


def test_hidden_longer_nfs_path_cannot_mask_opened_local_mount(tmp_path, monkeypatch):
    root, table, _, _ = kernel(tmp_path, monkeypatch, device="8:1")
    table.write_text(
        line(root, "9") + line(root.parent, fs="ext4", device="8:1", source="/dev/root")
    )
    assert filesystem_evidence(root, table)[0]["fstype"] == "ext4"


@pytest.mark.parametrize(
    "ids,device,message",
    [
        (("99", "99"), "0:41", "missing"),
        (("7", "8"), "0:41", "changed"),
        (("7", "7"), "8:1", "device differs"),
    ],
)
def test_changed_or_unverifiable_mount_fails_closed(tmp_path, monkeypatch, ids, device, message):
    root, table, calls, _ = kernel(tmp_path, monkeypatch, ids=ids, device=device)
    table.write_text(line(root))
    with pytest.raises(Blocked, match=message):
        filesystem_evidence(root, table)
    assert sum(kind == "open" for kind, _ in calls) == sum(kind == "close" for kind, _ in calls)


def configure_storage(root, table, monkeypatch, *, state="active"):
    # Constructor separately enforces /media. This uses portable temporary paths
    # so all synthetic mount cases also run in the Windows suite.
    store = Storage.__new__(Storage)
    store.root, store.mountinfo = root, table
    store.supervisor = SimpleNamespace(
        request=lambda _: {
            "mounts": [
                {
                    "name": "frigate",
                    "usage": "media",
                    "type": "nfs",
                    "state": state,
                    "user_path": str(root),
                    "server": "store",
                    "path": "/recordings",
                    "password": "must-never-appear",
                    "options": "secret=must-never-appear",
                }
            ]
        }
    )
    monkeypatch.setattr(storage_module, "safe_path", lambda path: path)
    return store


@pytest.mark.parametrize(
    "fs,state,source,blocked",
    [
        ("nfs4", "active", "store:/recordings", False),
        ("nfs", "active", "store:/recordings", False),
        ("autofs", "active", "store:/recordings", True),
        ("ext4", "active", "store:/recordings", True),
        ("nfs4", "inactive", "store:/recordings", True),
        ("nfs4", "active", "wrong:/recordings", True),
    ],
)
def test_validation_and_allowlisted_failure_evidence(
    tmp_path, monkeypatch, fs, state, source, blocked
):
    root, table, _, _ = kernel(tmp_path, monkeypatch)
    table.write_text(line(root, fs=fs, source=source))
    store = configure_storage(root, table, monkeypatch, state=state)
    if blocked:
        with pytest.raises(StorageBlocked) as error:
            store.validate()
        details = error.value.diagnostics
        assert details["opened_mount"]["fstype"] == fs
        assert details["supervisor_nfs_mounts"][0]["state"] == state
        assert "must-never-appear" not in str(details)
    else:
        result = store.validate()
        assert result["identity"]["source"] == source
        assert result["free"] == 143360


def test_unreachable_mount_reports_only_errno_and_safe_config(tmp_path, monkeypatch):
    root, table, _, _ = kernel(
        tmp_path, monkeypatch, failure=OSError(errno.EHOSTUNREACH, "sensitive details")
    )
    store = configure_storage(root, table, monkeypatch)
    with pytest.raises(StorageBlocked) as error:
        store.validate()
    assert error.value.diagnostics["os_error"] == errno.EHOSTUNREACH
    assert "sensitive" not in str(error.value)


def test_failed_validation_returns_details_without_stopping_frigate(env, monkeypatch):
    c, headers = client(env)
    before = env.db.read_bytes()
    details = {"media_path": "/media/frigate", "opened_mount": {"fstype": "ext4"}}

    def unavailable():
        raise StorageBlocked("NFS access not confirmed", details)

    monkeypatch.setattr(env.storage, "validate", unavailable)
    response = c.post("/api/validate", json={"target": SLUG}, headers=headers)
    assert response.status_code == 409
    assert response.json["diagnostics"] == details
    assert env.db.read_bytes() == before
    assert ("stop", SLUG) not in env.supervisor.calls


@pytest.mark.skipif(os.name != "posix", reason="Reads actual Linux fdinfo and mountinfo")
def test_real_kernel_descriptor_mount_and_capacity(tmp_path):
    mount, capacity = filesystem_evidence(tmp_path, Path("/proc/self/mountinfo"))
    info = tmp_path.stat()
    assert mount["device"] == f"{os.major(info.st_dev)}:{os.minor(info.st_dev)}"
    assert capacity["total"] > 0 and capacity["free"] <= capacity["total"]


def test_descriptor_without_mount_id_does_not_guess(monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda _: "pos:\t0\nflags:\t0100000\n")
    with pytest.raises(Blocked, match="mount ID"):
        storage_module.descriptor_mount_id(42)
