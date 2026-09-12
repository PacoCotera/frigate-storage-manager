# Changelog

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
