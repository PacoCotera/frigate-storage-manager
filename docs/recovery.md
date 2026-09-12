# Recovery and backups

**Cleanup/recovery mutations remain locked in 0.1.2.** This describes the implemented,
synthetically tested engine for a later validated maintenance release.

## Interrupted job

Startup detects nonterminal local jobs and blocks new work. It does not automatically
start Frigate or manipulate media. Closing a browser does not stop the worker.

1. Leave the selected Frigate stopped. Keep Start on boot, Watchdog and Auto update
   disabled until resolved. Home Assistant and HAOS can remain running.
2. Restore the **same** NFS mount. Do not substitute local storage or alter the recovery
   directory. Review the target, phase and error.
3. In an enabled release choose **Recover interrupted job**. It checks DB/config/version,
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
