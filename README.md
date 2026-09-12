# Frigate Storage Manager

An independent Home Assistant OS app for previewing and deleting Frigate recordings
and associated history, without changing retention. Includes a separate full reset.

**0.2.1 supports experimental cleanup and full reset for explicitly authorized users.**
Live discovery, NFS validation and previews have worked, and the operator has authorized
the first destructive test. Write access, lifecycle, cleanup and recovery are tested
with synthetic media/databases but remain unvalidated on the real HAOS installation.
Delivery and live evidence remain tracked in [issue #1](https://github.com/PacoCotera/frigate-storage-manager/issues/1).

## Install

In Home Assistant, open **Settings → Apps → App store → ⋮ → Repositories**
(older HA versions call these Add-ons). Add:

```text
https://github.com/PacoCotera/frigate-storage-manager#codex/frigate-storage-manager
```

The feature-branch suffix is required while the implementation PR is unmerged.
After merge, use the repository URL without the suffix. Install **Frigate Storage
Manager**, start it, and choose **Open Web UI**. Supervisor builds the amd64 image
from this repository; no registry image is required.

1. Select the installed Frigate app explicitly.
2. Validate its database, capture settings, Supervisor role and existing NFS mount.
3. Select cameras, a cutoff and optionally completed exports, then preview cleanup.
4. Review selected footage duration, recording time ranges, estimated space and
   what stays for each camera. Individual records are optional technical details.
   Reopening the page retrieves your active or saved preview.
5. For deletion, add your HA user ID to this app's `admin_user_ids`, restart the
   manager, and disable Frigate's Start on boot, Watchdog and Auto update. Validate
   again, create a fresh preview and confirm **Delete selected history**.

**Clear everything…** is a separate action. It ignores camera/date filters and
deletes all cameras' recordings, clips and exports, plus the local `frigate.db` and
SQLite sidecars. This intentionally removes bookmarks, history and Frigate database
users. Review the exact target and type `DELETE ALL FRIGATE DATA`. Configuration,
models and Home Assistant stay intact. Frigate creates a fresh database on restart.

Frigate continues recording/detection during previews. Read the
[installation guide and live checklist](frigate_storage_manager/DOCS.md).

## Topology

Proxmox hosts HAOS. Frigate and this manager run as separate HAOS apps. The manager
uses the existing NFS media mapping at `/media/frigate`; the separate storage server
continues to hold recordings. Frigate's live SQLite database stays local to HAOS.
No SSH, Proxmox guest commands, Docker socket, secondary NFS mount or installation
on the storage server is used.

The manifest requests `media` and `all_addon_configs` mappings plus Supervisor's
`manager` role. HAOS offers a broad configuration mapping; application operations
are restricted to the explicitly selected Frigate directory. The default database
is `frigate.db`, and its configured relative path must match Frigate's effective
`/config/...` path. Writable mappings support explicit disposable probes and the
maintenance operations. The user allowlist is empty by default; every deletion needs
a user-bound, one-use confirmation and fresh runtime checks.

Ingress accepts only Supervisor's gateway and authenticated user identity. Write
probes require a configured user-ID allowlist and CSRF token. No host port is exposed.
See [verified capabilities](docs/compatibility.md).

## Cleanup and recovery

The engine removes eligible recordings, completed events/timelines, review/status
records, associated images, previews and supported semantic vectors. Completed
exports are optional and use creation time. Bookmarks protect linked event/review
groups, and active history, padded footage, trigger references and in-progress exports
are preserved. Unknown schemas, missing indexed files and unsafe paths block cleanup.

For selected-history cleanup, the offline engine pauses only the selected Frigate app,
recalculates and rejects expanded scope, backs up metadata on NFS, stages files on
the same filesystem, and commits metadata with an in-transaction witness. Recovery
restores staging before commit or finishes deletion after commit. Ambiguity leaves
Frigate stopped. The worker continues independently of the browser.

Full reset stages the three Frigate media directories on NFS and SQLite files locally
by rename, validates the staged tree without loading it into RAM, then commits an
explicit durable reset marker before permanent removal. Recovery restores everything
before that marker, or finishes reset after it. The reset has no archive item cap;
large archives can take substantial time with Frigate stopped.

**A database backup cannot restore successfully deleted videos.** Read
[recovery and backup lifecycle](docs/recovery.md). Up to five backups are retained;
removal is explicit. Recordings and database backups are never copied onto HAOS's
system disk.

## Development

```sh
python -m venv .venv
# Activate .venv for your shell, then:
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
docker build --platform linux/amd64 -t frigate-storage-manager:dev frigate_storage_manager
```

The reference schema comes from all 32 migrations at Frigate 0.17.2 commit
`3d4dd3ac4b00e7257bd3412608a783001d7d77ed`. Tests use real SQLite, sqlite-vec 0.1.3
and synthetic files. CI tests Windows/Linux, reproduces that schema, builds the
amd64 image and boots it to check ingress isolation. These do not prove live HAOS access.

See [architecture](docs/architecture.md) and [validation evidence](docs/validation.md).
No video decoding, transcoding or periodic full-library scans are performed.

## License

The original [MIT license](LICENSE) is unchanged. Frigate schema attribution is in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
