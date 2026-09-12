# Validation evidence and remaining gates

Implementation follows [issue #1](https://github.com/PacoCotera/frigate-storage-manager/issues/1).

## Automated evidence

The original Windows development run passed **107 tests**; subsequent versions add
regressions. Platform/privilege checks also run in Linux CI. Current counts and CI
links are recorded in issue #1/PR and the version notes below.
They are not evidence of actual HAOS installation/access.

Coverage includes camera/time boundaries, historical cameras, bookmarks and transitive
groups, active/overlapping history and padding, optional exports, related metadata and
real sqlite-vec deletion, schema rejection, missing/unsafe files, failed/read-only NFS,
staging/database faults, lost commit responses, phase restart recovery, original app
state, concurrent/replayed jobs, scope expansion/shrinkage, lifecycle failures,
ingress/CSRF/allowlists, backup disposal, and bounded memory on 100,000 recordings.

Seven tests terminate a separate worker process with `os._exit` during staging,
database updates, immediately before/after WAL commit, purge and restart phases,
then recover with a fresh process context. The UI was also exercised in the browser
against a freshly generated synthetic installation: validation, camera discovery,
preview counts/protection, disabled deletion and layout passed without console errors.

Initial [PR CI run](https://github.com/PacoCotera/frigate-storage-manager/actions/runs/34659855363)
passed Linux tests, pinned-schema reproduction and amd64 image build/boot/ingress
isolation checks. The final PR head is tested again after hardening.

CI runs Linux/Windows tests, reproduces all 32 pinned upstream migrations, builds the
actual amd64 app context, verifies runtime dependencies and release gate, boots the
image and checks direct non-ingress rejection. None of these install into HAOS.

## Actual HAOS validation — in progress

- [x] Install, start and display the ingress UI on the existing HAOS VM (user screenshot).
- [ ] Verify ingress identity and write authorization.
- [x] Discover/select the actual Frigate identifier/version/state (0.1.2 UI evidence).
- [x] Read the supported local database and real schema (successful 0.1.2 previews).
- [x] Pass NFS validation and display capacity on the existing share (0.1.2 UI evidence).
- [ ] Validate disposable write access and restricted backup-directory permissions.
- [x] Produce read-only previews of real old recordings for one and two cameras.
- [ ] Inspect a known event/bookmark and its protected footage; confirm ongoing recording.
- [ ] Record sanitized evidence in issue #1 and review any differences.
- [x] Operator authorized destructive testing and a separate complete media/database reset; 0.2.0 enables the workflows with runtime checks.
- [ ] Complete a real lifecycle test and confirmed cleanup/reset.
- [ ] Verify real cleanup/recovery before claiming production readiness.

No live recordings have been deleted, real Frigate lifecycle changed, or live Frigate
configuration modified by this development work.

### 0.2.0: authorized maintenance and full reset

After successful live discovery/storage/previews, the operator explicitly requested
destructive testing and a button to delete all Frigate media plus `frigate.db`.
The release gate is enabled while the ingress user allowlist, CSRF, one-use frozen
confirmation, storage/version/configuration checks and lifecycle safeguards remain.
An empty default allowlist permits no mutations. Validation displays actionable
maintenance blockers, and disposable probes now test rename as well as write/read/delete.

Full reset intentionally overrides bookmark/history preservation only for its distinct
typed confirmation. Tests exercise the enabled HTTP boundary, operation/user binding,
all-camera/media/database removal, configuration preservation, durable staging/commit
recovery, real WAL sidecars, worker termination, changed mounts/paths, unsafe entries,
failed startup and streaming memory bounds. CI and browser results are recorded in
issue #1 and PR #2. These synthetic checks do not establish live deletion or recovery.

The local full suite passed 209 tests with six platform/privilege skips, followed by
one additional operation-binding pass and one Windows symlink-privilege skip. Eleven
JavaScript tests pass. Browser QA completed selected cleanup and full reset against
disposable synthetic data, checked typed confirmation, displayed active job progress
with recovery disabled, and retained completion after reload without console errors.
The unauthorised-user/automatic-start case displays each prerequisite while keeping
previews available. CI runs the final combined suite on both platforms and builds
the installable image; its exact results are recorded in the issue/PR.

### 2026-09-11: first live feedback and 0.1.1 fix

The user reported Core 2026.9.2, Supervisor 2026.09.0, HAOS 18.2 and Frontend
20260826.7. The 0.1.0 ingress UI loaded but validation reported that the media path
was not NFS; the user confirmed their share is NFS. The screenshot alone does not
identify which mount entry the app selected or prove real database/schema access.

Supervisor [2026.09.0 network mounts](https://github.com/home-assistant/supervisor/blob/2026.09.0/supervisor/mounts/mount.py)
use autofs and propagate them through the supported
[media mapping](https://github.com/home-assistant/supervisor/blob/2026.09.0/supervisor/docker/app.py).
The old longest-path selection can pick the hidden autofs entry at the same path
even when NFS is active. 0.1.1 instead opens the directory read-only, matches
[`fdinfo.mnt_id`](https://man7.org/linux/man-pages/man5/proc_pid_fdinfo.5.html) to
[`mountinfo`](https://man7.org/linux/man-pages/man5/proc_pid_mountinfo.5.html), and
checks the device and mount stability. It preserves source/state requirements.

Regression cases cover stacked autofs/NFS in either table order, dormant automount
activation, hidden longer paths, local fallback, missing/changing mount IDs,
device/source/state mismatches, unreachable mounts and credential-free failure
details. Linux CI also exercises real unprivileged `/proc` descriptor evidence.
The local 0.1.1 suite passed 125 tests with six platform/privilege skips. Browser QA
with `tools/ui_fixture.py --storage-error` confirmed that a failed validation opens
the diagnostic details, displays the actual filesystem and keeps preview/probe
controls disabled. Lint, formatting and JavaScript syntax checks passed.
Real NFS access and the explanation for this installation remain to be confirmed
by rerunning validation with 0.1.1. Deletion remains disabled.

### 0.1.2: hours cutoff

Added **Older than hours** with a 12-hour default and whole-hour precision. Four
JavaScript tests cover sub-day precision, validation bounds and elapsed time across
a daylight-saving change; CI runs them on Linux and Windows without npm dependencies.
Browser QA against a disposable synthetic installation reproduced the user's `0.5`
Days input, switched to Hours, and completed a 12-hour preview. Changing to 1 hour
hid the previous result. Switching to a specific date also completed a preview
despite the inactive invalid Days input. Bookmarks and the deletion lock remained
visible in both previews. No additional live HAOS access is inferred from this test.

### 0.1.2 live feedback and 0.1.3 preview improvements

User screenshots subsequently show successful discovery, local schema access, NFS
validation and one-/two-camera previews. A single-camera preview reportedly took
5–10 seconds. The earlier missing result/duplicate warning remains an unclassified
failure; the old UI did not retain enough evidence to diagnose it. Those successful
live selections contained recordings/previews, with no selected events/reviews or
bookmarks. They do not validate bookmark protection, write access or maintenance.

0.1.3 adds durable background previews, phase/elapsed/record progress, reconnection,
duplicate attachment, saved result retrieval and a metadata item inspector with
decision reasons. All selected records are inspectable; preserved examples are
explicitly capped at 500 per category. Details come from the selection snapshot.

New automated cases cover duplicate requests, response loss/reopening, user isolation,
failed thread start, visible error payloads, restart interruption and completed-result
persistence, expiry/retention, pagination, stable snapshots, bookmark/link/padding/
trigger explanations, semantic references and compatibility with offline cleanup.
The 100,000-record archive test now also includes inspection and keeps peak traced
Python allocations below 12 MiB. That bound excludes SQLite/native allocations and
does not estimate total HAOS memory or large-NFS latency.

Browser QA against disposable synthetic media observed immediate progress and disabled
controls, reloaded during a delayed preview and recovered its completed result,
followed a protected recording to its bookmark, and reopened a saved result after
editing the form. A simulated background failure remained visible after reopening,
with controls released and no JavaScript warnings/errors. The local Windows suite
passes 144 Python tests (six platform/privilege skips), plus four JavaScript tests;
lint, formatting and syntax checks pass. CI reruns all platform/image checks.
Live testing of 0.1.3 and the remaining maintenance gates still require the user.

### 0.1.4: understandable results and latency investigation

Live feedback from 0.1.3 confirms a completed result and durable selection-limit
failure, but the default individual-row listing was too technical and a subsequent
preview was much slower. The UI now presents camera footage duration, recorded time
ranges, estimated space and plain-language keep/remove decisions. Raw counts/IDs
are closed by default; full preserved totals remain separate from 25-example samples.

Tests cover overlapping/nested/gapped and cross-hour footage, multiple cameras,
size reconciliation, exact kept totals beyond the sample, owner-bound/expiring time
pages, known oversized selections before NFS file calls and cross-camera protected
file references. Browser QA uses thousands of synthetic recordings via
`tools/ui_fixture.py --many-recordings` to avoid validating only a tiny row listing.

`tools/benchmark_preview.py` generates 2,001 selected and 25,000 preserved synthetic
recordings plus 80 kept events. Locally, the old/new workers both finish in roughly
1–2 seconds; this does not reproduce or explain the live slowdown. The release
reduces diagnostic sampling and journal writes, batches reference queries, and saves
phase timings for the next live diagnosis. No claim of restored real-NFS latency is
made. Full CI evidence and remaining live gates are tracked in issue #1/PR #2.

Local validation passes 154 Python tests with six platform/privilege skips and seven
JavaScript tests. Lint, formatting and syntax pass. Browser QA of version 0.1.4
verified an initial summary with zero individual rows loaded, grouped ranges,
working diagnostics on explicit expansion, and visible phase timings without
JavaScript warnings/errors. Synthetic footage remained behind the disabled gate.
