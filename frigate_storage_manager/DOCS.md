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
| `admin_user_ids` | `[]` | HA user IDs authorized for probes, cleanup, full reset and recovery. Your ID appears under Cleanup access. |
| `max_plan_items` | `10000` | Combined selected-row/file cap, range 100–20000. Choose an earlier cutoff/fewer cameras if exceeded. |

Version **0.2.0 enables maintenance** after explicit operator authorization to proceed
with real cleanup testing. The empty user allowlist still blocks all mutations by
default. Add your HA user ID in Configuration, save and restart this manager.
Preview/discovery access is established; real cleanup/recovery is not yet validated.

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
   **Test write access**. It creates, fsyncs, reads, renames and removes a random 17-byte file
   in the media root and selected database parent. Existing media/rows stay untouched.
   A directory probe does not prove every descendant's rename/delete permissions.
7. Preview clearly old history. Inspect selected/preserved items and reasons, and verify
   recording/detection continued and configuration remained unchanged.
8. Record sanitized results in issue #1. Do not share full configurations, database
   backups, camera credentials, tokens or private footage.

Lifecycle validation reports declared `manager` permission, **not a completed stop/start
test**. Actual stop/start happens during the confirmed maintenance job, with state
verification before media/database changes. No additional release unlock is required.

## Delete selected history

### Frigate shows `error` after stopping

Version **0.2.1** handles a Supervisor exit-error status when its read-only stats
endpoint explicitly confirms the selected app is not running. Click **Validate
connection** again after updating the manager. The connection then shows
**Stopped (Supervisor reports an exit error; verified not running)**, and the exit
label no longer blocks maintenance. The worker repeats the check before touching
history and throughout recovery. Generic API errors, timeouts and unknown states
remain blocked; they do not prove Frigate stopped.

You can leave Frigate running and let the manager stop it after confirmation.
If it is already stopped, it stays stopped after the operation. Effective settings
must have been validated once while Frigate was running, with unchanged configuration.

### Steps

1. Add your HA user ID to `admin_user_ids` and restart this manager if not already done.
2. In the selected **Frigate** app's Info page, turn off **Start on boot**, **Watchdog**
   and **Auto update**. Leave Frigate running. The manager cannot change these Core-only
   Supervisor options; they prevent an automatic restart over an interrupted operation.
3. Back in the manager, **Validate connection**. The page lists each remaining blocker.
4. Create a fresh, small preview on one camera and confirm **Delete selected history**.
5. The job checks disposable write/rename/delete access before stopping Frigate, makes
   an NFS metadata backup, stages media, commits metadata and finishes removal. Watch
   **Jobs and recovery**; closing/reloading the page does not cancel the worker.
6. When the job resolves, restore Frigate's automatic settings as desired. Verify the
   result in Frigate before continuing with larger selections.

The optional probe button is useful for diagnosing permissions; the job repeats probes
automatically. Failed access checks reject the job before stopping Frigate. A failed
or interrupted operation stays visible and must be recovered before starting another.

## Clear everything: all media and database

Use **Clear everything…** when you want a complete history reset. It has its own review
and confirmation and **ignores camera/date/export selections** in the preview form.
The same administrator and Frigate automatic-setting requirements apply.

It deletes the complete `recordings`, `clips` and `exports` directories beneath the
validated Frigate media root, including all cameras, unindexed files within those
directories, snapshots, previews, face/trigger images, completed/in-progress exports
and bookmarked footage. It also deletes the selected local `frigate.db` and its
`-wal`, `-shm` and `-journal` sidecars. Database users, history, bookmarks and semantic
metadata are reset. Configuration files, model cache, Home Assistant and unrelated
media-share directories are outside the reset. Manager metadata backups are retained
for explicit disposal; they contain no copy of deleted video.

Click **Clear everything…**, review the exact paths and target, type
`DELETE ALL FRIGATE DATA`, then press **Delete all media and database**. Files written
until Frigate stops are included. It backs up metadata, stages media on NFS and SQLite
files locally without copying them onto the HAOS disk, validates the tree and records
a durable reset commit marker. Then it removes the staged data. There is no full-library
in-memory plan or per-item selection cap; scanning/deleting a large archive can take
a long time and Frigate stays stopped until resolved.

If Frigate was running, it restarts and creates a fresh database. Otherwise it remains
stopped until you start it. Removing database users may require using Frigate's initial
administrator setup again; Home Assistant users are unchanged. Recovery before reset
commit restores media and SQLite files; after commit it completes deletion. Do not
erase staging/journals or manually restart Frigate over an unresolved reset.

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
open **Frigate Storage Manager**, and update to **0.2.0**. Reopen its Web UI and
confirm the version in the notice. Frigate itself does not need an update or restart.

For maintenance, disable Frigate's **Start on boot, Watchdog and Auto
update** first. Restore them in HA only after the job resolves. This manager cannot
change Supervisor's Core-only system options. Preview does not require these changes.

See [recovery documentation](https://github.com/PacoCotera/frigate-storage-manager/blob/codex/frigate-storage-manager/docs/recovery.md).
