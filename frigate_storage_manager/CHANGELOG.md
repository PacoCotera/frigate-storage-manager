# Changelog

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
