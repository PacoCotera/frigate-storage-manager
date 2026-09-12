"""Only documented Supervisor operations and selected Frigate read APIs."""

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

from .database import supported_version
from .safety import Blocked, atomic_json, identifier, read_json, relative, safe_path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Blocked("API redirects are not permitted")


class Supervisor:
    def __init__(self, token=None):
        self.token = token if token is not None else os.environ.get("SUPERVISOR_TOKEN", "")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, path, *, action=False, frigate=None):
        if frigate:
            host = identifier(frigate).replace("_", "-")
            if path not in ("/api/config", "/api/version") or action:
                raise Blocked("Frigate API operation not allowed")
            url, headers = f"http://{host}:5000{path}", {}
        else:
            if not self.token:
                raise Blocked("Supervisor token unavailable; run this app inside HAOS")
            url = "http://supervisor" + path
            headers = {"Authorization": "Bearer " + self.token}
        req = urllib.request.Request(
            url, headers=headers, method="POST" if action else "GET", data=b"" if action else None
        )
        try:
            with self.opener.open(req, timeout=15) as response:
                body = response.read(4 * 1024 * 1024 + 1)
            if len(body) > 4 * 1024 * 1024:
                raise Blocked("API response is too large")
            if frigate and path == "/api/version":
                return body.decode().strip().strip('"')
            result = json.loads(body)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            raise Blocked("Selected service API is unavailable or refused the request") from None
        if frigate:
            return result
        if result.get("result") != "ok":
            raise Blocked("Supervisor did not confirm the operation")
        return result.get("data", {})

    def discover(self):
        result = []
        for app in self.request("/addons").get("addons", []):
            slug = identifier(app["slug"])
            # Proxy apps do not own a Frigate database.
            if (
                "frigate" in slug.lower()
                and "proxy" not in slug.lower()
                and "storage_manager" not in slug
            ):
                result.append({k: app.get(k) for k in ("slug", "name", "version", "state")})
        return result

    def info(self, slug):
        return self.request(f"/addons/{identifier(slug)}/info")

    def lifecycle(self, slug, action):
        if action not in ("start", "stop"):
            raise Blocked("Unsupported lifecycle action")
        desired = "started" if action == "start" else "stopped"
        error = None
        try:
            self.request(f"/addons/{identifier(slug)}/{action}", action=True)
        except Blocked as exc:
            error = exc  # The request may have succeeded despite a lost response.
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if self.info(slug).get("state") == desired:
                return
            time.sleep(1)
        raise Blocked(f"Supervisor did not verify Frigate {desired}") from error


def project_config(config):
    """Persist only maintenance inputs; never persist/return full live config."""

    def capture(record):
        result = {}
        for severity in ("alerts", "detections"):
            section = record.get(severity, {})
            for key in ("pre_capture", "post_capture"):
                value = section.get(key, 5)
                if type(value) is not int or not 0 <= value <= 86400:
                    raise Blocked("Unsupported capture interval")
                result[f"{severity}_{key}"] = value
        return result

    cameras = config.get("cameras")
    if not isinstance(cameras, dict) or len(cameras) > 256:
        raise Blocked("Invalid or excessive camera configuration")
    return {
        "database": config.get("database", {}).get("path", "/config/frigate.db"),
        "global_capture": capture(config.get("record", {})),
        "cameras": {
            identifier(name): capture(value.get("record", {})) for name, value in cameras.items()
        },
    }


class Installation:
    def __init__(self, supervisor, data, options, configs=Path("/addon_configs")):
        self.supervisor, self.data, self.options = supervisor, Path(data), options
        self.configs = Path(configs)

    def target(self, slug=None):
        slug = slug or self.options.get("target_slug")
        apps = self.supervisor.discover()
        if not slug:
            # Even one app must be explicitly selected before opening its DB.
            raise Blocked("Select the Frigate app explicitly")
        identifier(slug)
        if slug not in {a["slug"] for a in apps}:
            raise Blocked("Selected Frigate app is not installed")
        return self.supervisor.info(slug)

    def config_dir(self, slug):
        return safe_path(self.configs, identifier(slug))

    def config_hash(self, slug):
        root = self.config_dir(slug)
        names = [n for n in ("config.yml", "config.yaml") if (root / n).exists()]
        if len(names) != 1:
            raise Blocked("Expected exactly one Frigate config.yml or config.yaml")
        path = safe_path(root, names[0])
        if path.stat().st_size > 4 * 1024 * 1024:
            raise Blocked("Frigate configuration is too large")
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def resolve(self, slug, *, offline=False):
        info = self.target(slug)
        supported_version(info.get("version"))
        fingerprint = self.config_hash(slug)
        cache_path = safe_path(self.data, f"config-{identifier(slug)}.json", exists=False)
        if info.get("state") == "started" and not offline:
            runtime_version = self.supervisor.request("/api/version", frigate=slug)
            supported_version(runtime_version)
            config = project_config(self.supervisor.request("/api/config", frigate=slug))
            atomic_json(
                cache_path, {"hash": fingerprint, "config": config, "version": info["version"]}
            )
        else:
            if not cache_path.exists():
                raise Blocked(
                    "Run validation while Frigate is running to read its effective capture settings"
                )
            cache = read_json(cache_path)
            if cache["hash"] != fingerprint or cache["version"] != info["version"]:
                raise Blocked("Frigate configuration or version changed; validate while running")
            config = cache["config"]
        rel = str(relative(self.options.get("database_relative_path", "frigate.db")))
        if config["database"] != str(PurePosixPath("/config") / rel):
            raise Blocked(
                "Explicit database path does not match Frigate's effective /config database path"
            )
        db = safe_path(self.config_dir(slug), rel)
        return info, db, config, fingerprint

    def maintenance_blockers(self, info):
        # Supervisor's sys_options endpoint is Core-only; this app cannot safely
        # disable auto-start. Fail closed rather than promise reboot safety.
        blockers = []
        for key, expected, label in (
            ("boot", "manual", "Start on boot"),
            ("watchdog", False, "Watchdog"),
            ("auto_update", False, "Auto update"),
        ):
            if info.get(key) != expected or (expected is False and info.get(key) is not False):
                blockers.append(f"Turn off Frigate {label} in its Home Assistant app settings.")
        if info.get("state") not in ("started", "stopped"):
            blockers.append("Wait for Frigate to reach a stable started or stopped state.")
        own = self.supervisor.info("self")
        if not own.get("hassio_api") or own.get("hassio_role") != "manager":
            blockers.append("Supervisor manager role is required.")
        return blockers

    def maintenance_ready(self, info):
        blockers = self.maintenance_blockers(info)
        if blockers:
            raise Blocked(" ".join(blockers))
