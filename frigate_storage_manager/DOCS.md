# Installation and validation

## Requirements and options

Use amd64 HAOS with current Supervisor ingress user identity support, internal
Frigate 0.17.2, and an NFS media share already configured through HAOS Network Storage.
Frigate's database must reside in its supported public `addon_config` directory.
The manager does not mount, relocate, or repair storage.

Install from the repository/feature branch in README, start the app and choose
**Open Web UI**. No inbound port or additional login password is configured.

| Option | Default | Meaning |
|---|---|---|
| `target_slug` | empty | Optional explicit Frigate identifier; otherwise select in the page. Never automatically chosen. |
| `database_relative_path` | `frigate.db` | Relative path inside the selected configuration directory, matching Frigate's effective `/config/...` database path. |
| `media_path` | `/media/frigate` | Existing NFS media directory beneath `/media`. Frigate's indexed `/media/frigate` paths are translated to this explicit root. |
| `admin_user_ids` | `[]` | HA user IDs authorized for disposable probes and future maintenance. Your ID appears under Validation details. |
| `max_plan_items` | `10000` | Combined selected-row/file cap, range 100–20000. Choose an earlier cutoff/fewer cameras if exceeded. |

There is **no deletion-enable option** in 0.1.0. Options and environment variables
cannot unlock it. Enablement requires a reviewed release after live validation.

## Real HAOS checklist

Keep Frigate running for first validation: its internal read-only API supplies
effective capture/database settings. Only maintenance inputs are cached, never full
configurations or passwords. A stopped app can be previewed after validation if its
configuration hash and version still match that cache.

1. Confirm installation, startup and ingress access.
2. Explicitly select the actual Frigate identifier and verify version/state.
3. Click **Validate connection**. Check the selected database path and schema.
4. Verify the reported NFS source is the existing remote share and capacity matches
   the media filesystem. An unavailable share/local fallback must block validation.
5. Confirm current and historical cameras. Disabled cameras present in configuration
   use their current capture settings; removed cameras use global capture padding.
6. Optionally add your HA user ID to `admin_user_ids`, restart this manager, and use
   **Test write access**. It creates, fsyncs, reads and removes a random 17-byte file
   in the media root and selected database parent. Existing media/rows stay untouched.
   A directory probe does not prove every descendant's rename/delete permissions.
7. Preview clearly old history. Inspect selection/protection counts and verify
   recording/detection continued and configuration remained unchanged.
8. Record sanitized results in issue #1. Do not share full configurations, database
   backups, camera credentials, tokens or private footage.

Lifecycle validation reports declared `manager` permission, **not a real stop/start
test**. A real lifecycle and small-cleanup test require later explicit approval.

## Dates and scope

The page shows browser timezone and UTC boundary. Days mean 24-hour periods. Local
times convert to UTC; nonexistent DST times are rejected, and repeated DST times use
the first occurrence displayed. End time must be strictly before the cutoff; touching
and crossing recordings survive. Completed export age uses creation time.

There are no independent file-only/metadata-only deletion switches. Capture protection
uses the greater alert/detection padding. A kept event/review protects its entire
linked group. Because in-progress exports have no source-interval field, their camera's
source history is conservatively kept. Triggers preserve their referenced events.
Unindexed orphan files, training images, faces, models and logs are outside scope.

## Troubleshooting and upgrades

- **Database mismatch:** verify slug and relative path. Private app `/data` cannot
  substitute for `addon_config`. No host-access workaround is used.
- **NFS unavailable/source mismatch:** inspect HA Network Storage. Kernel and
  Supervisor must agree on one active share. Different source aliases fail closed.
- **Probe fails:** inspect permissions/read-only settings. The manager never changes
  recording permissions or storage-server configuration.
- **Unsupported schema/version:** keep diagnostics; do not bypass the checks.
- **Long outage:** hard NFS can block file IO. Local job status stays separate, but a
  worker may need manager restart after storage recovers. Do not start Frigate over
  unresolved staging.
- **Upgrade:** resolve active jobs first and retain manager `/data` plus matching
  media manifests/backups. Revalidate after Frigate/HAOS updates. Never downgrade or
  erase persistent data during an unresolved job.

For a later maintenance release, disable Frigate's **Start on boot, Watchdog and Auto
update** first. Restore them in HA only after the job resolves. This manager cannot
change Supervisor's Core-only system options. Preview does not require these changes.

See [recovery documentation](https://github.com/PacoCotera/frigate-storage-manager/blob/codex/frigate-storage-manager/docs/recovery.md).
