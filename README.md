# Frigate Storage Manager

A Home Assistant OS app for previewing and deleting old Frigate recordings and their associated history without changing retention settings.

**Status: specification and development. This repository is not yet an installable app.**

The complete requirements, delivery sequence, and acceptance criteria are tracked in **[issue #1: Build Frigate Storage Manager](https://github.com/PacoCotera/frigate-storage-manager/issues/1)**.

## Intended setup

| Component | Responsibility |
|---|---|
| Proxmox | Hosts the Home Assistant OS VM |
| Home Assistant OS | Runs Frigate and Frigate Storage Manager; holds the live Frigate database |
| Separate NFS server | Stores media through the existing HAOS network-storage mount |

The first target installation uses HAOS VM 103 and a separate NFS server named `storepush`. Frigate sees that server's media at `/media/frigate`. These are deployment details, not hard-coded application requirements.

**Recordings stay on the NFS server. The live database stays in HAOS.**

The manager is designed to run alongside Frigate and use the media mount HAOS already provides. It will access the selected Frigate app's database through a supported directory mapping and use Supervisor for lifecycle control. It must not require host SSH, Proxmox guest commands, a Docker socket, or software installed on the NFS server.

## Planned workflow

1. Select the Frigate installation and validate its database and media mount.
2. Choose cameras and an age or date cutoff.
3. Preview the affected history and estimated media space recoverable.
4. Confirm deletion.
5. The manager pauses Frigate, cleans media and associated metadata together, and restores its prior running state when the operation is consistent.

Home Assistant remains running. Preview does not pause Frigate or modify its existing media or database.

## Planned cleanup scope

- Recording segments and their database entries.
- Completed object events and related timeline records.
- Event snapshots and thumbnails.
- Review alerts/detections, thumbnails, and review-status records.
- Eligible preview videos and supported semantic-search references.
- Completed exports only when explicitly included.

Bookmarked events, active events, and footage required by preserved history remain protected. Existing camera, detector, MQTT, and retention configuration stays unchanged.

## Recovery requirements

A cleanup job must coordinate the database and NFS files through durable, recoverable staging. Interrupted jobs must be recoverable, and Frigate must not automatically restart when consistency is unresolved.

Metadata backups belong on the media share rather than the HAOS system disk. **A database backup cannot restore successfully deleted video.**

Mount validation must distinguish an unavailable NFS share from an empty library. Storage reporting must show the media filesystem's capacity, not the HAOS VM disk.

## Initial compatibility and delivery

The initial target is the existing `amd64` HAOS installation and the verified Frigate **0.17.2** schema, including build suffixes. Other versions must not permit destructive cleanup until reviewed.

The first milestone is an installable app with ingress, discovery, storage validation, and read-only previews. Validate that milestone on the actual HAOS installation before enabling destructive operations. The full cleanup and recovery implementation can be developed and tested behind a disabled gate while live validation is pending.

Synthetic tests do not establish compatibility with the actual installation. The build must distinguish implemented behavior, automated validation, and live HAOS validation.

## Development

Start with [issue #1](https://github.com/PacoCotera/frigate-storage-manager/issues/1). It contains the complete architecture, interface behavior, selection rules, failure handling, security requirements, test cases, and acceptance criteria.

Verify the relevant Home Assistant directory mappings, Supervisor permissions, and pinned Frigate schema before implementing destructive maintenance. Keep the interface simple and the backend lightweight; no video decoding, transcoding, or continuous full-library scans.

A previous standalone cleanup script was a prototype only. It is not part of this repository or a prerequisite for development.

Installation instructions, build configuration, tests, CI, and release documentation will be added with the implementation.

## License

See [LICENSE](LICENSE).
