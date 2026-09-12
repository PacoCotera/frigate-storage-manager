# Architecture and invariants

Flask and Waitress serve one ingress page with four HTTP threads. A non-daemon worker
owns cleanup independently of the browser. Only selected Frigate read APIs
`/api/config` and `/api/version` on internal port 5000 are used; tokens stay server-side.
Redirects and proxy environment variables cannot forward credentials elsewhere.

`Installation` validates target, database and projected settings. `Storage` checks
kernel/Supervisor NFS agreement. `planner` builds a read-only snapshot. `Engine` and
`JobStore` coordinate recovery; `web` enforces ingress, per-user CSRF, allowlists and
the release gate.

`PreviewService` runs one read-only daemon worker and stores owner-bound task receipts
in local SQLite. POST returns a task ID immediately; status polling never touches
NFS. A restart interrupts unfinished previews without resuming a scan. Completed
plans retain metadata-only inspection from the same SQLite read snapshot, alongside
the existing confirmation digest. The offline engine compares row/file identities,
not presentation details. Preview submission and cleanup reservation serialize their
active-state checks in the same state database.

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
- Four small task receipts; progress writes at most once per two seconds plus phase changes.
- Inspection includes every selected record and at most 25 preserved records per
  primary-history category. Each response has at most 50 rows. Decoding saved plans
  is serialized across HTTP threads. No thumbnail, vector payload or event JSON is
  copied into inspection, and preserved samples cause no extra NFS file reads.
- Camera overview counts cover all preserved history, independently of the detail
  sample. Selected footage duration unions intervals per camera; time-window details
  group by start hour and are fetched only on expansion, 12 groups per page. Raw
  metadata is also loaded only on explicit expansion. No main-database writes occur.
- Protected path references are checked in five batched queries against a capped
  temporary selected-path table; existing path indexes remain usable.
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

`DESTRUCTIVE_ENABLED` is true in 0.2.0 after real discovery/preview access and explicit
operator authorization to start destructive testing. The default user allowlist is
empty. HTTP handlers and the engine still enforce authorization, confirmation and
runtime checks. Read-only validation releases 0.1.x had this constant disabled.

## Full reset protocol

Full reset is a distinct operation with a distinct one-use, user-bound confirmation
and a typed phrase. A cleanup token cannot authorize reset or vice versa. It clears
all cameras/bookmarks in Frigate 0.17.2's three media trees (`recordings`, `clips`,
`exports`) and the selected local database with SQLite sidecars. Configuration and
unrelated media-share directories stay outside its scope. New media up to the stop
is explicitly included; the date/camera form is not a reset filter.

The shared job reservation and worker mutex exclude concurrent cleanup/reset/recovery.
After probes and verified stop, it saves a coherent metadata backup on NFS and a
small immutable manifest containing the three directory identities and at most four
SQLite file identities. Media directories move by rename to NFS staging. Database
files move by rename within their original local parent, so the database is never
moved onto NFS and no extra database copy is made on the HAOS disk.

Before committing, a postorder iterator validates every staged media entry without
materializing the tree; memory and open iterators are bounded by depth (64 maximum).
Paths, symlinks/reparse points, hardlinks, nested filesystems and nonregular entries
are checked before any irreversible removal and again while purging. The full reset
is not constrained by the preview item cap and can require substantial downtime.

An atomic, fsynced `committed.json` marker inside local `.fsm-reset-<job>` is the
explicit point of no return. Without it, recovery restores all staged directories and
SQLite files. With a matching marker/manifest, it completes streaming deletion and
removes the staged database files. Conflicting/missing evidence, changed mounts or
unexpected reappearance of original paths leaves Frigate stopped. Restart is persisted
separately and can be retried without touching a newly created database. Markers and
metadata backups remain available until explicit completed-backup disposal.
