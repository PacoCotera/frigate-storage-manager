"""Ingress-only HTTP boundary. Supervisor credentials never enter responses."""

import hashlib
import hmac
import mimetypes
import secrets
import threading
import time

from flask import Flask, g, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from . import DESTRUCTIVE_ENABLED, VERSION
from .database import connect, validate_schema
from .jobs import TERMINAL
from .planner import camera_names
from .previews import PreviewService
from .safety import Blocked, identifier
from .storage import StorageBlocked, probe_directory


def create_app(installation, storage, store, engine):
    # Browser modules require a JavaScript MIME type, including on hosts whose
    # MIME registry incorrectly labels .mjs as plain text.
    mimetypes.add_type("text/javascript", ".mjs")
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=16384)
    secret = secrets.token_bytes(32)  # A restart deliberately expires browser CSRF tokens.
    previews = PreviewService(installation, storage, store)
    app.extensions["previews"] = previews

    def csrf(user, issued=None):
        issued = issued or str(int(time.time()))
        mac = hmac.new(secret, f"{user}:{issued}".encode(), hashlib.sha256).hexdigest()
        return issued + "." + mac

    @app.before_request
    def authenticate():
        # Ignore X-Forwarded-For. No host port is published by the manifest.
        if request.remote_addr != "172.30.32.2":
            return jsonify(error="Home Assistant ingress is required"), 403
        user = request.headers.get("X-Remote-User-Id", "")
        if not user or len(user) > 128:
            return jsonify(error="Ingress user identity unavailable; update HA/Supervisor"), 403
        g.user = user
        if request.method == "POST":
            if not request.is_json or request.headers.get("Sec-Fetch-Site") == "cross-site":
                return jsonify(error="JSON and a same-site ingress request are required"), 403
            token = request.headers.get("X-FSM-CSRF", "")
            try:
                issued = token.split(".")[0]
                valid = 0 <= time.time() - int(issued) < 900 and hmac.compare_digest(
                    csrf(user, issued), token
                )
            except (ValueError, OverflowError):
                valid = False
            if not valid:
                return jsonify(error="Request token expired; refresh the page"), 403

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'"
        )
        return response

    @app.errorhandler(Blocked)
    def blocked(error):
        body = {"error": str(error)}
        if isinstance(error, StorageBlocked):
            body["diagnostics"] = error.diagnostics
        return jsonify(body), 409

    @app.errorhandler(Exception)
    def failed(error):
        if isinstance(error, HTTPException):
            return jsonify(error=error.name), error.code
        # Paths/configuration/service response bodies can contain credentials.
        app.logger.error("Operation failed: %s", type(error).__name__)
        return jsonify(error="Operation failed; storage or service access needs investigation"), 503

    def administrator():
        if g.user not in installation.options.get("admin_user_ids", []):
            raise Blocked(
                "Add your HA user ID to admin_user_ids in this app's configuration for write probes or maintenance"
            )

    def idle():
        if any(j["phase"] not in TERMINAL for j in store.jobs()):
            raise Blocked("Resolve the interrupted/active job before a new validation or preview")

    @app.get("/")
    def index():
        return render_template("index.html", version=VERSION)

    @app.get("/api/status")
    def status():
        jobs = store.jobs()
        public = [
            {
                k: j[k]
                for k in (
                    "id",
                    "target",
                    "phase",
                    "summary",
                    "processed",
                    "error",
                    "recovery_error",
                    "recovery_required",
                    "original_running",
                    "media_bytes_removed",
                    "backup",
                    "backup_removed",
                    "completed_at",
                )
                if k in j
            }
            for j in jobs[:20]
        ]
        return jsonify(
            version=VERSION,
            csrf=csrf(g.user),
            user_id=g.user,
            destructive_enabled=DESTRUCTIVE_ENABLED,
            is_admin=g.user in installation.options.get("admin_user_ids", []),
            jobs=public,
            recovery_required=any(j["phase"] not in TERMINAL for j in jobs),
            previews=previews.recent(g.user),
            preview_busy=previews.busy(),
        )

    @app.get("/api/discovery")
    def discovery():
        return jsonify(
            apps=installation.supervisor.discover(),
            configured_target=installation.options.get("target_slug", ""),
            media_path=str(storage.root),
        )

    @app.post("/api/validate")
    def validate():
        idle()
        slug = identifier(request.json.get("target"))
        info, db_path, config, fingerprint = installation.resolve(slug)
        evidence = storage.validate()
        db = connect(db_path)
        try:
            vectors = validate_schema(db, info["version"])
            cameras = camera_names(db, config)
        finally:
            db.close()
        own = installation.supervisor.info("self")
        return jsonify(
            target=slug,
            version=info["version"],
            state=info["state"],
            database=str(db_path),
            media=evidence,
            cameras=cameras,
            historical_cameras=[c for c in cameras if c not in config["cameras"]],
            vectors=vectors,
            lifecycle={
                "role": own.get("hassio_role"),
                "declared_access": own.get("hassio_api") is True
                and own.get("hassio_role") == "manager",
                "stop_start_tested": False,
                "boot": info.get("boot"),
                "watchdog": info.get("watchdog"),
                "auto_update": info.get("auto_update"),
            },
            write_access="Not tested; use the disposable probe if authorized",
        )

    @app.post("/api/probe")
    def probe():
        administrator()
        idle()
        slug = identifier(request.json.get("target"))
        _, db, _, _ = installation.resolve(slug)
        storage.probe()
        probe_directory(db.parent)
        return jsonify(
            media_write=True,
            database_directory_write=True,
            message="Disposable probes removed. Existing media and database rows were not modified.",
        )

    @app.post("/api/preview")
    def preview():
        return jsonify(previews.submit(request.json, g.user)), 202

    @app.get("/api/previews/<token>")
    def preview_status(token):
        return jsonify(previews.get(token, g.user))

    @app.get("/api/previews/<token>/items")
    def preview_items(token):
        try:
            page = int(request.args.get("page", "0"))
        except ValueError:
            raise Blocked("Invalid inspection page") from None
        return jsonify(
            previews.items(
                token,
                g.user,
                request.args.get("view", "selected"),
                request.args.get("kind", ""),
                request.args.get("search", ""),
                page,
            )
        )

    @app.get("/api/previews/<token>/hours")
    def preview_hours(token):
        try:
            page = int(request.args.get("page", "0"))
        except ValueError:
            raise Blocked("Invalid time-window page") from None
        return jsonify(previews.hours(token, g.user, request.args.get("camera", ""), page))

    @app.post("/api/delete")
    def delete():
        administrator()
        if not DESTRUCTIVE_ENABLED:
            raise Blocked("Deletion is release-locked until real HAOS validation is complete")
        return jsonify(
            engine.submit(request.json.get("preview_id"), g.user, request.json.get("confirmation"))
        ), 202

    @app.post("/api/recover")
    def recover():
        administrator()
        if not DESTRUCTIVE_ENABLED:
            raise Blocked("Recovery mutations are release-locked")
        # Recovery also runs independently of the browser connection.
        token = request.json.get("job_id")
        store.get(token)

        def worker():
            try:
                engine.recover(token)
            except Exception as exc:
                app.logger.error("Recovery requires attention: %s", type(exc).__name__)

        threading.Thread(target=worker, name="recovery", daemon=False).start()
        return jsonify(accepted=True), 202

    @app.post("/api/backups/remove")
    def remove_backup():
        administrator()
        if not DESTRUCTIVE_ENABLED:
            raise Blocked("Backup removal is release-locked")
        return jsonify(engine.remove_backup(request.json.get("job_id")))

    return app
