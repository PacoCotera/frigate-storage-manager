# Validation evidence and remaining gates

Implementation follows [issue #1](https://github.com/PacoCotera/frigate-storage-manager/issues/1).

## Automated evidence

The Windows development run passed **107 tests**, with five Linux/symlink-specific
checks deferred to Linux CI. Final counts and CI links are recorded in issue #1/PR.
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
- [ ] Discover/select the actual Frigate identifier/version/state.
- [ ] Verify the supported local database mapping and real schema.
- [ ] Confirm the remote NFS source, availability and media filesystem capacity.
- [ ] Validate disposable write access and restricted backup-directory permissions.
- [ ] Preview real old history and inspect counts/protection without interruption.
- [ ] Record sanitized evidence in issue #1 and review any differences.
- [ ] Review gate enablement, lifecycle test and a small explicitly approved cleanup.
- [ ] Verify real cleanup/recovery before claiming production readiness.

No live recordings have been deleted, real Frigate lifecycle changed, or live Frigate
configuration modified by this development work.

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
