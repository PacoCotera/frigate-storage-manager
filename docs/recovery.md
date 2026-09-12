# Recovery and backups

**0.2.0 supports authorized cleanup, full reset and recovery.** These workflows are
tested with synthetic media/SQLite; real HAOS maintenance validation is still pending.

## Interrupted job

Startup detects nonterminal local jobs and blocks new work. It does not automatically
start Frigate or manipulate media. Closing a browser does not stop the worker.

1. Leave the selected Frigate stopped. Keep Start on boot, Watchdog and Auto update
   disabled until resolved. Home Assistant and HAOS can remain running.
2. Restore the **same** NFS mount. Do not substitute local storage or alter the recovery
   directory. Review the target, phase and error.
3. Choose **Recover interrupted job** once the active worker has stopped. It checks DB/config/version,
   mount identity, manifest digest and the transactional commit witness.
4. Without a commit, original rows must match and staging is restored. After commit,
   selected rows must be absent and staging is purged. Either action can be retried.
5. Contradictory evidence, changed/missing paths or files leave Frigate stopped for
   investigation. No finally block blindly restarts it.

Originally stopped Frigate stays stopped. A failed start after consistent cleanup
leaves `restarting`; recovery retries the state change without repeating cleanup.

## Stored evidence

| Location | Contents |
|---|---|
| Manager `/data/state.sqlite` | One-use previews, jobs, original state, bindings, manifest digests and results |
| Manager `/data/config-<slug>.json` | Projected capture settings and config hash, never full credentials/config |
| Media `.frigate-storage-manager/<job>/metadata.sqlite` | Consistent stopped database backup |
| Media `.frigate-storage-manager/<job>/manifest.json` | Immutable selection and recovery binding |
| Media `.frigate-storage-manager/<job>/staged/` | Same-filesystem renamed media pending recovery/commit |
| Live Frigate DB `_fsm_commit` | Matching job/digest witness written with deletion, replaced by the next stopped cleanup |

## Full reset recovery

**Clear everything** intentionally removes bookmarks and all history along with the
database. Its evidence differs from selected-history cleanup because that database
cannot hold the reset's surviving commit witness.

- Media directories are staged under the job's NFS `staged/` directory.
- SQLite files are staged by rename in `.fsm-reset-<job>/` beside the selected local
  database. This makes no extra local database copy. Keep this directory intact.
- The immutable NFS manifest records the original identities. A matching local
  `committed.json` marks the irreversible reset decision.
- Before the marker, recovery restores media and SQLite files. After it, recovery
  finishes deleting staged media and SQLite files. A contradictory journal/marker
  or replaced path blocks recovery and leaves Frigate stopped.
- Once resolved, originally running Frigate restarts and creates a fresh database.
  Originally stopped Frigate remains stopped. If startup failed, recovery retries it
  without removing or replacing a newly created database.

Use **Recover interrupted job** for both operations. Do not manually start Frigate,
erase its local staging directory, or restore an old database over a completed reset.
Completed-backup removal also removes the small local reset marker and empty folder.
The NFS metadata backup does not contain the deleted videos or images.

Keep `/data` and matching media evidence across upgrades/restarts. Backups can contain
camera/event/authentication metadata; retain restricted ownership/modes and never
publish them. NFS ACL/root-squash failures block operations rather than change share permissions.

## Completed backup lifecycle

The Jobs panel exposes retained backups. **Remove completed metadata backup** removes
only its known backup/manifest and empty directories; never a recursive tree. It
refuses while any job is unresolved. Removal can be retried after interruption.
Partially created backups from resolved failed jobs are also visible for disposal.

Five retained backups is the ceiling. A sixth job is blocked until explicit removal.
No age-based silent deletion occurs.

**A metadata backup cannot restore successfully deleted videos. Never restore an old
database alone after successful deletion:** it recreates references to missing footage.
Full restoration needs a matching complete media backup/snapshot and metadata, under
a separately reviewed procedure.

## Ambiguous or damaged evidence

Missing witnesses with changed rows, corrupt manifests, replaced databases, changed
mount identities, unexpected sources after commit and missing staging all require
investigation. Device/inode checks are not silently relaxed after HAOS reboot; a reboot
  can intentionally require manual reconciliation. The manager never automatically
restores a database backup.

If `/data` is lost, do not start a new cleanup or start Frigate over possible staging.
Media manifests/backups provide forensic evidence; importing unknown job state or
guessing commit outcome is outside automatic recovery. File a sanitized issue with
the phase/error. Do not erase the remaining evidence.
