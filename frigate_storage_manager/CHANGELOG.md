# Changelog

## 0.2.0

- Enable explicitly confirmed maintenance after live discovery/preview validation
  and operator authorization to proceed with destructive testing.
- Add **Clear everything** with a separate target review and typed confirmation.
  It removes all Frigate recordings, clips and exports plus its local SQLite database
  and sidecars, including bookmarks/history/database users. Configuration stays intact.
- Stage full-reset media directories on NFS and database files locally by rename;
  recover before/after a durable reset commit marker. Stream large trees with bounded
  memory and verify filesystem/path identities before removal.
- Show actionable authorization/lifecycle prerequisites, disposable rename checks,
  readable job phases and completion results. Disable recovery while a worker is active.
- Test enabled cleanup/reset/recovery HTTP routes and real worker interruption;
  actual HAOS cleanup/reset remains pending rather than claimed as validated.

## 0.1.4

- Replace the default row listing with camera summaries: selected footage duration,
  recording time ranges, recoverable space, and plain-language removal/keep decisions.
- Group recording details by starting hour, loading at most 12 time ranges per page
  when expanded. Camera duration excludes gaps and double-counted overlapping footage.
- Collapse individual IDs, counts, prior previews and timing diagnostics by default.
  Keep exact preserved totals, while reducing diagnostic examples to 25 per category.
- Store per-phase timings and reduce progress journal writes to once every two
  seconds plus phase changes. The live latency increase remains under investigation.
- Check protected file references in five batched queries and reject known oversized
  selections before per-file NFS access. Explain the combined record/file limit.
- Keep every cleanup/recovery release gate disabled.

## 0.1.3

- Run previews in one background worker with durable phase, elapsed time and
  record progress. Disable repeated submission and reattach to the existing task.
- Retrieve active/completed previews after reopening ingress. Retain four recent
  task receipts and four bounded result snapshots; item details expire after 15 minutes.
- Inspect all selected records and a clearly labelled preserved sample, including
  IDs, cameras, times, decision reasons, related records and selected media paths/sizes.
- Report failed/interrupted previews visibly and log phases, duration and error type
  without camera identifiers, paths or exception payloads.
- Cover duplicate/lost responses, restart persistence, expiry, user isolation,
  bookmark/overlap explanations and bounded inspection of a large synthetic archive.
- Keep deletion and recovery mutations locked.

## 0.1.2

- Add an Older than hours cutoff with one-hour precision and a 12-hour default.
- Disable inactive cutoff inputs so an invalid days value cannot block an hours
  or specific-date preview. Changing the method or hours expires the shown preview.
- Test relative cutoff precision, bounds and elapsed time across daylight-saving changes.
- Keep deletion and recovery mutations locked.

## 0.1.1

- Fix NFS detection for HAOS automounts and stacked mounts by matching the opened
  directory's Linux mount ID instead of choosing the first longest path match.
- Activate existing automounts through read-only directory access, measure capacity
  on that descriptor, and reject a mount changed during validation.
- Show selected kernel and Supervisor evidence when storage validation fails.
  Do not include credentials or raw mount options in those diagnostics.
- Keep deletion and recovery mutations locked.

## 0.1.0

- Package an independent amd64 HAOS app with ingress, supported media/configuration
  mappings and Supervisor lifecycle integration.
- Add explicit target discovery, local database/NFS validation, capacity reporting
  and disposable write probes.
- Preview coherent history cleanup while preserving bookmarks and required footage.
- Implement offline cleanup, same-filesystem staging, restricted metadata backups,
  durable commit witnesses, original-state restoration and interrupted-job recovery.
- Add schema-provenance, fault, security, resource and image-build checks.
- Keep destructive operations source-code locked pending actual HAOS validation.
