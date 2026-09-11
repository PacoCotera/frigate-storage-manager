# Verified capabilities

Source inspection verifies supported interfaces. Actual HAOS access remains a
separate gate in issue #1.

| Capability | Primary evidence | Implementation |
|---|---|---|
| Settings and mappings | [HA app configuration](https://developers.home-assistant.io/docs/apps/configuration/) documents `/data`, `media`, `addon_config`, `all_addon_configs` and permissions | Own `/data`; media mapping; explicit `/addon_configs` mapping restricted in application to selected slug |
| Shared configuration mapping | [Supervisor docker/app.py](https://github.com/home-assistant/supervisor/blob/b44b4acbc21768765f70089c90d1a4d9daee5d48/supervisor/docker/app.py) | Public configuration root is supported; private Frigate `/data` is not assumed accessible |
| Lifecycle role | [Supervisor security allowlist](https://github.com/home-assistant/supervisor/blob/b44b4acbc21768765f70089c90d1a4d9daee5d48/supervisor/api/middleware/security.py) | `manager` is the least available role for discovery and selected `/addons/<slug>/start`/`stop`; `default` only permits info |
| Ingress | [HA ingress documentation](https://developers.home-assistant.io/docs/apps/presentation/#ingress), [proxy source](https://github.com/home-assistant/supervisor/blob/b44b4acbc21768765f70089c90d1a4d9daee5d48/supervisor/api/ingress.py) | Require peer `172.30.32.2` and authenticated user header; no forwarded-IP trust; allowlist and CSRF supplement sidebar admin setting |
| Mount validation | [Supervisor mounts API](https://github.com/home-assistant/supervisor/blob/b44b4acbc21768765f70089c90d1a4d9daee5d48/supervisor/api/mounts.py) | Compare active NFS media usage, source and `user_path` with kernel mountinfo; report capacity on that filesystem |

No `admin` role, host networking, privileged access, Docker socket, Core API or SSH.
`sys_options` is Core-only; maintenance requires boot/watchdog/auto-update safeguards
already configured in HA. Lost lifecycle responses are followed by state verification.
The Dockerfile uses an explicit pinned base/labels, not legacy `build.yaml` fallback.

## Frigate 0.17.2 contract

Pinned source: `3d4dd3ac4b00e7257bd3412608a783001d7d77ed`.
The [HAOS manifest](https://github.com/blakeblackshear/frigate-hass-addons/blob/915c716135743facd557f538f2463551df212106/frigate/config.yaml)
at `915c716135743facd557f538f2463551df212106` specifies 0.17.2 and maps both
`media:rw` and `addon_config:rw`.

- [Models](https://github.com/blakeblackshear/frigate/blob/v0.17.2/frigate/models.py)
  and [migrations 001–032](https://github.com/blakeblackshear/frigate/tree/v0.17.2/migrations)
  define the schema. `tools/generate_schema.py` executes every migration; CI checks
  reproducibility. Review links use `data.detections`, timeline `source_id`, review
  status `review_segment_id`; `retain_indefinitely` marks bookmarks.
- [Constants](https://github.com/blakeblackshear/frigate/blob/v0.17.2/frigate/const.py)
  set the local database default `/config/frigate.db`.
- [Review deletion](https://github.com/blakeblackshear/frigate/blob/v0.17.2/frigate/api/review.py),
  [event cleanup](https://github.com/blakeblackshear/frigate/blob/v0.17.2/frigate/events/cleanup.py)
  and [recording cleanup](https://github.com/blakeblackshear/frigate/blob/v0.17.2/frigate/record/cleanup.py)
  cover different subsets; no single reviewed endpoint implements this entire scope.
- [Image helpers](https://github.com/blakeblackshear/frigate/blob/v0.17.2/frigate/util/file.py)
  define JPG snapshots, clean WEBP/legacy PNG variants and external event thumbnails.
  [Exports](https://github.com/blakeblackshear/frigate/blob/v0.17.2/frigate/record/export.py)
  use `exports` videos and `clips/export` thumbnails, with an `in_progress` flag but
  no source-interval field.
- [Vector integration](https://github.com/blakeblackshear/frigate/blob/v0.17.2/frigate/db/sqlitevecq.py)
  uses `vec_thumbnails` and `vec_descriptions`, each 768-float cosine embeddings.
  [The build](https://github.com/blakeblackshear/frigate/blob/v0.17.2/docker/main/build_sqlite_vec.sh)
  pins sqlite-vec 0.1.3; this app uses the same version and validates reproduced backing
  table definitions. Backing tables are never deleted independently.

Extra tables, columns, foreign keys, triggers, views or vector layouts fail closed.
Historical schema variations require review even with a matching version string.
The only manager-owned live-DB extension is the bounded `_fsm_commit` witness table,
used only by the disabled offline maintenance engine.
