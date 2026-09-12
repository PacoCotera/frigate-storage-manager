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

## Actual HAOS validation — pending

- [ ] Install and start on the existing amd64 HAOS VM.
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
