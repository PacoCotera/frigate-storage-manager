# Architecture and invariants

Flask and Waitress serve one ingress page with four HTTP threads. A non-daemon worker
owns cleanup independently of the browser. Only selected Frigate read APIs
`/api/config` and `/api/version` on internal port 5000 are used; tokens stay server-side.
Redirects and proxy environment variables cannot forward credentials elsewhere.

`Installation` validates target, database and projected settings. `Storage` checks
kernel/Supervisor NFS agreement. `planner` builds a read-only snapshot. `Engine` and
`JobStore` coordinate recovery; `web` enforces ingress, per-user CSRF, allowlists and
the release gate.

## Selection and resource bounds

Candidate events/reviews are pruned to a fixed point across their links. A kept member
preserves the whole group. Bookmarks, unfinished history, trigger references and
in-progress exports protect their footage. Eligible recordings cannot overlap any
kept padded interval; previews additionally cannot overlap kept recordings.

Age uses database boundaries. File size/mtime/device/inode are identity checks only.
Missing indexed files, hardlinks, symlinks, reparse points, nested filesystems and
paths outside expected categories block the operation.

- No media walks, decoding, transcoding, retention changes or periodic archive scans.
- One preview at a time; 10,000 selected rows/files combined by default, maximum 20,000.
  Limit failure gives no partial approval.
- Four previews at most, each at most 16 MiB, with 15-minute validity.
- SQLite 4 MiB page cache, file-backed temporary relationships, 120-second SQL budget,
  and at most 256 distinct cameras. Large archives are not materialized in Python.
- `/data` holds settings, bounded previews and small jobs. Media/backups stay on NFS.
- Five retained backups maximum; explicit disposal before another job. No hidden pruning.
- Keep 100 recent resolved receipts plus the current job and retained backups; consumed/expired preview
  IDs remain unusable even after an old receipt is removed.
- Job status uses local state and does not touch NFS. Hard NFS can block syscalls beyond
  application deadlines; the manager does not claim it can cancel or repair such IO.

## Job protocol

1. Freeze target/version, DB identity, config hash, camera/cutoff/export scope, row
   signatures and file identities in a user-bound, one-use preview.
2. Atomically consume it in local SQLite and reject an active job. A process lifetime
   `flock` prevents duplicate manager processes; a worker mutex serializes threads.
3. Revalidate/probe and persist original running state **before** Supervisor stop.
   Verify stopped; Home Assistant and the VM remain running.
4. Recalculate offline. New rows/files or changed signatures require a new preview;
   strict subsets are permitted.
5. Create a consistent metadata backup and immutable fsynced manifest on NFS, with
   0600 files inside 0700 directories. Incompatible permissions block maintenance.
6. Persist staging intent, then rename each file to a deterministic staging name on
  the same filesystem. Linux uses no-follow directory descriptors and parent fsync.
  Supervisor/configuration rechecks occur at most two seconds or 128 files apart,
  plus every phase boundary; each file still receives its own path/identity checks.
7. Persist committing intent; use `BEGIN IMMEDIATE`, verify selected row signatures,
   delete known children/parents with bound parameters, and insert `_fsm_commit` in
   that same transaction. Commit uses FULL durability. Only the current witness is kept.
8. Recovery reads the witness and verifies row outcomes before touching staging.
   Absent witness plus intact originals means rollback; matching witness plus absent
   rows means finish deletion. Disagreement is ambiguous and prevents restart.
9. Verify again, persist restarting intent, restore original app state and record the
   result. A failed start can be retried without repeating deletion.

The NFS server must honor fsync/rename. External actors must not start Frigate or edit
its DB while stopped. Observed configuration, lifecycle, mount or identity changes
block progress. Storage rollback/external changes can require manual reconciliation.

`DESTRUCTIVE_ENABLED` is false without an option/environment override. Both production
engine and HTTP handlers enforce it. Tests inject a gate only into temporary synthetic
fixtures, which are excluded from the image. Real access/preview evidence and an
explicitly approved small cleanup precede any reviewed enablement release.
