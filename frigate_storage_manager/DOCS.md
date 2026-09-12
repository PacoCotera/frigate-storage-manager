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

There is **no deletion-enable option** in 0.1.4. Options and environment variables
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
7. Preview clearly old history. Inspect selected/preserved items and reasons, and verify
   recording/detection continued and configuration remained unchanged.
8. Record sanitized results in issue #1. Do not share full configurations, database
   backups, camera credentials, tokens or private footage.

Lifecycle validation reports declared `manager` permission, **not a real stop/start
test**. A real lifecycle and small-cleanup test require later explicit approval.

## Progress and saved previews

Click **Preview cleanup** once. **Preview status** appears immediately with the
current phase and elapsed time; the controls stay disabled while it runs. The media
checking phase also reports processed/total selected records. Other phases use an
indeterminate indicator because their total work is not known in advance.

You can close or reload the ingress page and return to the same task. A duplicate
request from your HA user attaches to the running preview with its original scope.
Other users cannot retrieve its result. The manager performs one preview at a time.

After completion, **Review the selection** shows estimated space and the duration of
selected camera footage. Each camera explains what would be removed and kept. If no
events/reviews are selected, the page states that explicitly; bookmarks are reported only if present.
All preserved counts cover the full snapshot, including history outside the examples.

**See recording time ranges** groups segments by the hour they start, at most 12
ranges per page. Ranges can contain gaps; displayed footage duration excludes gaps
and overlapping time. Camera totals also remove overlaps between different ranges.
Each file's complete size belongs to its start-hour group. The total is camera
footage, not elapsed wall-clock time across multiple cameras. No video is loaded.

**Technical details: counts and individual records → Browse individual records**
retains searchable IDs, camera names, times, reasons and selected media paths/sizes.
All selected records remain available within the plan limit. Individual preserved
examples are limited to **25 per category**, prioritizing bookmarks, then unfinished
and oldest history. The page labels sample/total counts. Search and related-record
lookup cover these retained examples only; absence from the sample does not mean
an item would be deleted. These diagnostics are closed by default.

**Previous previews and technical information → Recent previews → Show saved preview**
reopens an earlier result, including after
editing the form. At most four task receipts and four result snapshots are retained
across all users. Item details remain available for 15 minutes after completion,
unless replaced sooner; an expired summary is labelled and cannot authorize cleanup.
Saved results describe the original snapshot, so validate and create a fresh preview
for a new selection. Previews do not create entries under **Jobs and recovery**.

Errors appear in the saved task. An app restart during a preview marks it
**interrupted**; create a new preview after resolving any storage issue. Completed
results survive restart within their retention limits. Phase, duration and error
type are also written to this app's logs. Completed phase durations are available
under **Previous previews and technical information**, making slow phases identifiable
without searching logs. Progress is persisted every two seconds and at phase changes.
Hard NFS outages can still block file IO;
the elapsed indicator is not an IO cancellation or completion guarantee.

## Dates and scope

Choose **Older than hours** for a whole number of hours (minimum 1, default 12),
**Older than days** for whole days, or **Specific date and time** for a cutoff to
the minute. For half a day, choose 12 hours. The page shows browser timezone and
the resulting UTC boundary before previewing. Hours are elapsed 60-minute periods;
days mean elapsed 24-hour periods, including across daylight-saving changes. Local
times convert to UTC; nonexistent DST times are rejected, and repeated DST times use
the first occurrence displayed. End time must be strictly before the cutoff; touching
and crossing recordings survive. Completed export age uses creation time.

Increasing Hours/Days chooses an earlier cutoff and selects less history. The safety
limit counts database records and media files separately: a recording row and its
file use two items. Choose fewer cameras or increase the age if the selection is too
large. A known oversized row selection is rejected before checking individual files.

There are no independent file-only/metadata-only deletion switches. Capture protection
uses the greater alert/detection padding. A kept event/review protects its entire
linked group. Because in-progress exports have no source-interval field, their camera's
source history is conservatively kept. Triggers preserve their referenced events.
Unindexed orphan files, training images, faces, models and logs are outside scope.

## Troubleshooting and upgrades

- **Database mismatch:** verify slug and relative path. Private app `/data` cannot
  substitute for `addon_config`. No host-access workaround is used.
- **NFS unavailable/source mismatch:** open **Validation details**, including on
  failed validation. Compare `media_path`, `opened_mount.fstype`/`source`, and
  `supervisor_nfs_mounts`. Kernel and Supervisor must agree on one active share.
  Different source aliases fail closed. These details include private server/share
  names; redact them before posting publicly, keeping matching values consistent.
  Version 0.1.0 could incorrectly select an `autofs` entry underneath a working NFS
  mount on Supervisor 2026.09.0. Update this manager to 0.1.1 or later and revalidate first.
  No Frigate restart, share recreation, or server configuration change is needed
  for this app fix. If it still fails, retain the new evidence for investigation.
- **Probe fails:** inspect permissions/read-only settings. The manager never changes
  recording permissions or storage-server configuration.
- **Unsupported schema/version:** keep diagnostics; do not bypass the checks.
- **Long outage:** hard NFS can block file IO. Local job status stays separate, but a
  worker may need manager restart after storage recovers. Do not start Frigate over
  unresolved staging.
- **Upgrade:** resolve active jobs first and retain manager `/data` plus matching
  media manifests/backups. Revalidate after Frigate/HAOS updates. Never downgrade or
  erase persistent data during an unresolved job.

To install these preview improvements, refresh the app store repository information,
open **Frigate Storage Manager**, and update to **0.1.4**. Reopen its Web UI and
confirm the version in the notice. Frigate itself does not need an update or restart.

For a later maintenance release, disable Frigate's **Start on boot, Watchdog and Auto
update** first. Restore them in HA only after the job resolves. This manager cannot
change Supervisor's Core-only system options. Preview does not require these changes.

See [recovery documentation](https://github.com/PacoCotera/frigate-storage-manager/blob/codex/frigate-storage-manager/docs/recovery.md).
