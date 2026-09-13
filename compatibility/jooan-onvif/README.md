# Jooan U8W ONVIF XML compatibility investigation

A documented workaround for `ActionNotSupported` during Frigate ONVIF initialization, with a small reference implementation for developers to review and adapt.

**Status:** an installation-specific workaround has enabled manual pan/tilt on one Jooan U8W camera with Frigate 0.18.0. This sanitized reference implementation has synthetic tests; it has not itself been installed on that camera. It is not an upstream Frigate release or a universal ONVIF fix.

This folder is an independent compatibility investigation hosted in the Frigate Storage Manager repository. It does not modify the storage manager app. Code is covered by the repository's [MIT license](../../LICENSE).

## Problem and observed result

The camera advertised an ONVIF device service on TCP port 8899 at `/onvif/device_service`. Frigate's settings page remained on **Loading profiles…**, while the backend repeatedly logged:

```text
Onvif connection failed for cam_01: ActionNotSupported
ONVIF initialization failed for cam_01
```

Inspection of the running installation located the failure in `frigate/ptz/onvif.py`, in `_init_onvif()`, while awaiting `onvif.update_xaddrs()`. The installed library implementation requested `GetCapabilities({"Category": "All"})` before creating media services. It failed before profile enumeration or movement commands.

Parsing and reserializing the outgoing SOAP XML with explicit namespace prefixes made discovery, profiles, and PTZ capability requests succeed. A narrowly scoped patch installed in a derived Frigate image then enabled manual camera movement in the Frigate UI.

## Environment and limits of the evidence

| Component | Observed environment |
| --- | --- |
| Camera | Operator identifies model as Jooan U8W, dual fixed/movable lens |
| Camera firmware | Exact version not captured; behavior may vary by firmware |
| Frigate | Running installation reports 0.18.0 |
| Base image | `ghcr.io/blakeblackshear/frigate@sha256:82d7475ba7e5f950911bd9d06f2392de96881f39eeacd78824a526dde9b5cad9` |
| Runtime | Docker on Ubuntu; NVIDIA detector image |
| ONVIF library | Imported as `onvif`, asynchronous API using aiohttp; distribution/version not captured |
| Authentication | Saved camera credentials; WS-Security PasswordDigest succeeds with normalized requests |
| Confirmed scope | One camera: initialization, profiles, advertised PTZ capabilities and actual manual movement |

Six cameras advertised discovery endpoints. The workaround was subsequently prepared for all six, but the available report does **not** confirm that rollout or movement on the remaining five. There was no controlled auto-tracking test. Reported relative-movement support alone does not establish Frigate auto-tracking compatibility. The operator reports no camera zoom support even though zoom buttons appear. Do not treat this as confirmation of zoom, presets, focus, or release-to-stop behavior.

## Diagnostic evidence

These are transcribed test outcomes reported by the operator, not raw packet captures or a controlled accuracy benchmark. Network addresses, credentials and ingress URLs are intentionally excluded.

| Test | Result |
| --- | --- |
| Library `GetCapabilities`: All, Device, Media, PTZ | All failed with `ActionNotSupported` |
| Library `GetServices` | `ActionNotSupported` |
| Frigate alternative PasswordText authentication setting | Did not resolve initialization; reverted |
| Hand-built SOAP 1.2 `GetSystemDateAndTime` | HTTP 200, expected response |
| Hand-built SOAP 1.2 `GetCapabilities` with PasswordDigest | HTTP 200, expected response |
| Same hand-built operations with SOAP 1.1 | HTTP 200, expected responses |
| Original captured library XML replayed through direct HTTP | HTTP 400, `ActionNotSupported` |
| Same captured XML parsed/reserialized and replayed through direct HTTP | HTTP 200, expected capabilities response |
| Normalized requests through the library | Discovery, `GetProfiles`, and `GetNodes` passed |
| Derived image running Frigate | Six video feeds healthy; cam_01 ONVIF features available |
| Manual UI control on cam_01 | Operator confirmed the movable lens moved |

Normalized profile enumeration returned `profile_0` / `MainStream` and `profile_1` / `SubStream`, both with PTZ configurations. Nodes `ptz_node_0` and `ptz_node_1` advertised continuous and relative pan/tilt spaces. These names are not a guarantee of how other firmware exposes its lenses.

The most useful comparison was replaying the captured request before and after serialization through the **same direct HTTP client**. It makes a transport-only explanation less likely. It does not isolate a single XML lexical difference: namespace prefixes, unused namespace declarations, whitespace, and empty-element formatting can all change during reserialization. We have not proved that any one prefix is required, that the library emits invalid SOAP, or that the issue occurs on every U8W.

## How the deployed workaround works

The installation-specific patch wrapped `aiohttp.ClientSession._request` inside the Frigate process. For an explicit camera address, port 8899 and `/onvif/` paths only, it:

1. Parses a SOAP 1.2 POST body with Python ElementTree.
2. Registers explicit prefixes (`s`, `tds`, `trt`, `tptz`, `tt`, `wsse`, `wsu`).
3. Serializes the XML again. Element names and ordinary text values, including the existing UsernameToken digest, remain the same for the tested requests.
4. Removes a stale `Content-Length` header so the HTTP client computes the new byte length.
5. Delegates the actual request to the original HTTP client.

No camera password change, TLS bypass, video-processing change or camera movement command is part of this normalization. The `PasswordDigest` text is preserved; this is **not** a claim that arbitrary XML signatures survive serialization. XML Signature requests must not be rewritten.

The patch lives in a small derived image built from a pinned Frigate image. Compose selects that local image, so recreating the container retains the fix. Upgrading the base image requires rebuilding and retesting it. A local monkey patch of a private aiohttp method is a workaround, not the preferred upstream architecture.

## Reference code and tests

[`jooan_onvif_xml.py`](jooan_onvif_xml.py) separates the serializer from the optional aiohttp adapter and requires an explicit endpoint allowlist. It defaults to no automatic installation, includes no real camera addresses, and adds guards against signed XML and QName-valued attributes that ElementTree cannot safely rewrite generically. Those guards are additional review protections, not findings from the camera tests.

Run the synthetic tests with Python 3.11 or newer, without installing Frigate, Docker, aiohttp, or camera credentials:

```sh
python -m unittest discover -s compatibility/jooan-onvif -p 'test_*.py' -v
```

For an isolated developer test in an environment that already has aiohttp:

```python
from jooan_onvif_xml import install

# Documentation-only address. Replace with an endpoint you own and have verified.
restore = install({("http", "192.0.2.10", 8899)})
try:
    # Run the ONVIF library's read-only discovery/profile requests here.
    # Do not issue movement commands during discovery testing.
    pass
finally:
    restore()
```

This is not a ready-to-run Frigate installer. The private deployment scripts also controlled systemd, SSD checks, local Compose paths and migration state; publishing those as a general installer would make unsafe assumptions about other deployments. The reference module does not alter a running installation unless explicitly installed in that process.

ElementTree namespace registration is process-global. The request rewrite is endpoint-scoped, but this registration can affect other ElementTree serializers in the process. The adapter also depends on a private aiohttp API. Both deserve review before broader adoption. Do not apply this to arbitrary SOAP traffic or assume preservation of namespace references inside text values.

## Suggested upstream investigation

1. Reproduce on the same firmware and record the camera firmware, `onvif` distribution/version, zeep and aiohttp versions.
2. Capture sanitized failing/passing requests. Remove credentials, UsernameToken data, serial numbers, IP addresses and private paths before sharing them.
3. Vary one serialization detail at a time. Determine whether prefix names, namespace placement, unused declarations, empty-element style or another detail causes the rejection.
4. Prefer an opt-in per-camera/per-service compatibility option at the ONVIF SOAP serialization boundary over globally wrapping aiohttp.
5. Add regression fixtures from that minimized case and confirm conformant cameras remain unaffected.
6. Test profile selection, short manual moves **and Stop**, presets, time-offset handling, faults and reconnection. Assess auto-tracking independently and only when requested.

The maintainers can reuse this MIT-licensed investigation and reference code, but the live result should not be represented as a fully reviewed upstream fix.

## Deployment and recovery lessons

The tested installer backed up the previous Compose image selection, built the derived image, validated imports/configuration offline, and restarted Frigate only after those checks. It then required healthy video feeds and nonempty ONVIF features over repeated polls. It retained the previous image and generated a rollback command. No database or recording restoration was needed because the change concerned request formatting and image selection.

For a future deployment, retain the prior image reference and configuration before changing anything. If checks fail, restore that selection and restart using the installation's normal lifecycle manager. Do not replace or delete the database to troubleshoot ONVIF.

## References

- [Frigate manual PTZ configuration and authentication options](https://docs.frigate.video/configuration/cameras/#setting-up-camera-ptz-controls)
- [ONVIF device service operations](https://www.onvif.org/yaml/wsdl-viewer2.php)
- [Frigate project](https://github.com/blakeblackshear/frigate)

Current online documentation may differ from the installed version. The initialization behavior above was established by inspecting the running installation's source and testing the camera.
