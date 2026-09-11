"""Read-only selection. SQLite does interval/relationship work; plans are capped.

The only writes on this connection are TEMP tables. Main is opened mode=ro,
including during offline recalculation. There is no filesystem directory scan.
"""

import base64
import math
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone

from .database import VECTORS, connect, validate_schema
from .safety import Blocked, cutoff_utc, digest, file_identity, identifier

TABLES = (
    "event",
    "reviewsegment",
    "recordings",
    "previews",
    "export",
    "timeline",
    "userreviewstatus",
)


def row_signature(row):
    return digest(
        {
            k: base64.b64encode(v).decode() if isinstance(v, bytes) else v
            for k, v in dict(row).items()
        }
    )


def camera_names(db, config):
    names = set(config["cameras"])
    for table in ("event", "reviewsegment", "recordings", "previews", "export"):
        for row in db.execute(f'SELECT DISTINCT camera FROM "{table}"'):
            names.add(row[0])
            if len(names) > 256:
                raise Blocked("More than 256 historical cameras requires compatibility review")
    return sorted(identifier(n) for n in names)


def build_plan(
    db_path, version, storage, config, cameras, cutoff, include_exports=False, max_items=10000
):
    if (
        type(include_exports) is not bool
        or not isinstance(cameras, list)
        or not 1 <= len(cameras) <= 256
    ):
        raise Blocked("Choose cameras and a valid export policy")
    if len(set(cameras)) != len(cameras):
        raise Blocked("Duplicate camera selection")
    cutoff_ts = cutoff_utc(cutoff)
    evidence = storage.validate()
    plan = {
        "scope": {
            "cameras": sorted(cameras),
            "cutoff": cutoff_ts,
            "cutoff_utc": datetime.fromtimestamp(cutoff_ts, timezone.utc).isoformat(),
            "include_exports": include_exports,
        },
        "mount": evidence["identity"],
        "rows": {},
        "files": [],
        "counts": {},
        "preserved": {},
        "bytes": 0,
        "warnings": [],
        "vectors": [],
    }
    count = 0

    def bounded():
        nonlocal count
        count += 1
        if count > max_items:
            raise Blocked(
                f"Selection exceeds the {max_items} item safety limit; choose an earlier cutoff or fewer cameras"
            )

    db = connect(db_path)
    try:
        plan["vectors"] = validate_schema(db, version)
        db.execute("BEGIN")  # One consistent WAL-aware live read snapshot.
        names = camera_names(db, config)
        if any(c not in names for c in cameras):
            raise Blocked("Unknown camera; refresh camera discovery")
        db.execute(
            "CREATE TEMP TABLE selected_camera (camera TEXT PRIMARY KEY, pre REAL, post REAL)"
        )
        for name in cameras:
            cap = config["cameras"].get(name, config["global_capture"])
            pre = max(cap["alerts_pre_capture"], cap["detections_pre_capture"])
            post = max(cap["alerts_post_capture"], cap["detections_post_capture"])
            db.execute("INSERT INTO selected_camera VALUES (?,?,?)", (name, pre, post))
        # Use the greater alert/detection padding for events AND reviews. This
        # deliberately retains extra boundary footage rather than guessing severity.
        for table in ("event", "reviewsegment", "recordings", "previews"):
            invalid = db.execute(f'''SELECT 1 FROM "{table}" JOIN selected_camera USING(camera)
                WHERE typeof(start_time) NOT IN ('real','integer') OR start_time < 0
                OR (end_time IS NOT NULL AND (typeof(end_time) NOT IN ('real','integer')
                  OR end_time < start_time OR end_time > 1e12)) LIMIT 1''').fetchone()
            if invalid:
                raise Blocked("Invalid temporal metadata; no safe cleanup selection")
        bad_json = db.execute("""SELECT 1 FROM reviewsegment
            WHERE NOT json_valid(data) OR CASE WHEN json_valid(data) THEN
              json_type(data) != 'object' OR json_type(data,'$.detections') IS NOT 'array'
              ELSE 1 END LIMIT 1""").fetchone()
        if bad_json:
            raise Blocked("Unsupported review detection relationships")
        if db.execute("""SELECT 1 FROM event JOIN selected_camera USING(camera)
            WHERE length(thumbnail)>1048576 OR length(data)>1048576 LIMIT 1""").fetchone():
            raise Blocked("An event exceeds the supported metadata size")
        db.execute("CREATE TEMP TABLE links (review TEXT, event TEXT, PRIMARY KEY(review,event))")
        db.execute("""INSERT OR IGNORE INTO links SELECT reviewsegment.id,j.value FROM reviewsegment,
            json_each(reviewsegment.data,'$.detections') j WHERE j.type='text' """)
        db.execute("CREATE INDEX temp.link_event ON links(event)")
        if db.execute("""SELECT 1 FROM reviewsegment,json_each(reviewsegment.data,'$.detections') j
                          WHERE j.type!='text' LIMIT 1""").fetchone():
            raise Blocked("Invalid review event identifiers")
        for table in TABLES:
            db.execute(f"CREATE TEMP TABLE pick_{table} (id PRIMARY KEY)")
        for table in ("event", "reviewsegment"):
            conditions = "AND retain_indefinitely=0" if table == "event" else ""
            # An in-progress export has no interval column. Preserve the camera's
            # entire source history until export completes.
            db.execute(
                f'''INSERT INTO pick_{table} SELECT id FROM "{table}" t
                JOIN selected_camera USING(camera)
                WHERE end_time < ? {conditions} AND NOT EXISTS
                (SELECT 1 FROM export x WHERE x.camera=t.camera AND x.in_progress!=0)
                LIMIT ?''',
                (cutoff_ts, max_items + 1),
            )
            if db.execute(f"SELECT count(*) FROM pick_{table}").fetchone()[0] > max_items:
                raise Blocked(
                    "Too many old history items; select an earlier cutoff or fewer cameras"
                )
        # Trigger configuration/history remains intact, including its referenced
        # event. Bookmarks propagate through the entire event/review group.
        db.execute("""DELETE FROM pick_event WHERE id IN
            (SELECT triggering_event_id FROM trigger WHERE triggering_event_id IS NOT NULL)""")
        while True:
            a = db.execute("""DELETE FROM pick_reviewsegment WHERE id IN
                (SELECT l.review FROM links l JOIN event e ON e.id=l.event
                 WHERE e.id NOT IN (SELECT id FROM pick_event))""").rowcount
            b = db.execute("""DELETE FROM pick_event WHERE id IN
                (SELECT l.event FROM links l WHERE l.review NOT IN (SELECT id FROM pick_reviewsegment))""").rowcount
            if not a + b:
                break
        protected = """NOT EXISTS (SELECT 1 FROM event e WHERE e.camera=t.camera
                AND e.id NOT IN (SELECT id FROM pick_event)
                AND e.start_time-c.pre <= t.end_time
                AND coalesce(e.end_time+c.post,1e30) >= t.start_time)
            AND NOT EXISTS (SELECT 1 FROM reviewsegment r WHERE r.camera=t.camera
                AND r.id NOT IN (SELECT id FROM pick_reviewsegment)
                AND r.start_time-c.pre <= t.end_time
                AND coalesce(r.end_time+c.post,1e30) >= t.start_time)
            AND NOT EXISTS (SELECT 1 FROM export x WHERE x.camera=t.camera AND x.in_progress!=0)"""
        db.execute(
            f"""INSERT INTO pick_recordings SELECT t.id FROM recordings t
            JOIN selected_camera c USING(camera) WHERE t.end_time < ? AND {protected}
            LIMIT ?""",
            (cutoff_ts, max_items + 1),
        )
        db.execute(
            f"""INSERT INTO pick_previews SELECT t.id FROM previews t
            JOIN selected_camera c USING(camera) WHERE t.end_time < ? AND {protected}
            AND NOT EXISTS (SELECT 1 FROM recordings r WHERE r.camera=t.camera
                AND r.id NOT IN (SELECT id FROM pick_recordings)
                AND r.start_time<=t.end_time AND r.end_time>=t.start_time) LIMIT ?""",
            (cutoff_ts, max_items + 1),
        )
        if include_exports:
            db.execute(
                """INSERT INTO pick_export SELECT id FROM export JOIN selected_camera USING(camera)
                WHERE in_progress=0 AND typeof(date) IN ('integer','real') AND date < ? LIMIT ?""",
                (cutoff_ts, max_items + 1),
            )
        db.execute(
            """INSERT INTO pick_timeline SELECT rowid FROM timeline WHERE source_id IN
            (SELECT id FROM pick_event) LIMIT ?""",
            (max_items + 1,),
        )
        db.execute(
            """INSERT INTO pick_userreviewstatus SELECT id FROM userreviewstatus
            WHERE review_segment_id IN (SELECT id FROM pick_reviewsegment) LIMIT ?""",
            (max_items + 1,),
        )
        files = {}

        def add_file(rel, kind, required=True):
            path = storage.file(rel, exists=required)
            if not path.exists():
                return  # Only optional clean snapshot variants may be absent.
            identity = file_identity(path)
            if rel in files:
                raise Blocked("Multiple selected metadata records reference the same media file")
            bounded()
            files[rel] = {"path": rel, "kind": kind, "identity": identity}

        for table in TABLES:
            key = "rowid" if table == "timeline" else "id"
            rows = []
            for row in db.execute(
                f'SELECT {key} AS _key,* FROM "{table}" WHERE {key} IN (SELECT id FROM pick_{table}) ORDER BY {key}'
            ):
                bounded()
                rows.append({"id": row["_key"], "signature": row_signature(row)})
                if table == "event":
                    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", row["id"]):
                        raise Blocked("Unsafe event media identifier")
                    name = f"{identifier(row['camera'])}-{row['id']}"
                    add_file(f"clips/{name}.jpg", "snapshots", bool(row["has_snapshot"]))
                    for ext in ("webp", "png"):
                        add_file(f"clips/{name}-clean.{ext}", "snapshots", False)
                    if not row["thumbnail"]:
                        add_file(
                            f"clips/thumbs/{row['camera']}/{row['id']}.webp", "event_thumbnails"
                        )
                elif table in ("recordings", "previews"):
                    add_file(storage.media_relative(row["path"], table), table)
                elif table == "reviewsegment":
                    add_file(
                        storage.media_relative(row["thumb_path"], "review_thumbnails"),
                        "review_thumbnails",
                    )
                elif table == "export":
                    add_file(storage.media_relative(row["video_path"], "exports"), "exports")
                    add_file(
                        storage.media_relative(row["thumb_path"], "export_thumbnails"),
                        "export_thumbnails",
                    )
            plan["rows"][table] = rows
            plan["counts"][table] = len(rows)
        for table in plan["vectors"]:
            rows = []
            # vec0 0.1.3 supports id IN (values), not every subquery plan.
            for event in plan["rows"]["event"]:
                row = db.execute(
                    f'SELECT id,{VECTORS[table]} FROM "{table}" WHERE id=?', (event["id"],)
                ).fetchone()
                if row:
                    bounded()
                    rows.append({"id": row["id"], "signature": row_signature(row)})
            plan["rows"][table] = rows
            plan["counts"][table] = len(rows)
        plan["files"] = [files[p] for p in sorted(files)]
        plan["bytes"] = sum(f["identity"]["size"] for f in plan["files"])
        plan["counts"].update(
            Counter(f["kind"] for f in plan["files"] if f["kind"] not in ("recordings", "previews"))
        )
        for table in ("event", "reviewsegment", "recordings", "previews", "export"):
            plan["preserved"][table] = db.execute(f'''SELECT count(*) FROM "{table}"
                JOIN selected_camera USING(camera) WHERE id NOT IN (SELECT id FROM pick_{table})''').fetchone()[0]
        plan["preserved"]["bookmarks"] = db.execute("""SELECT count(*) FROM event
            JOIN selected_camera USING(camera) WHERE retain_indefinitely!=0""").fetchone()[0]
        plan["preserved"]["active_events"] = db.execute("""SELECT count(*) FROM event
            JOIN selected_camera USING(camera) WHERE end_time IS NULL""").fetchone()[0]
        plan["preserved"]["active_reviews"] = db.execute("""SELECT count(*) FROM reviewsegment
            JOIN selected_camera USING(camera) WHERE end_time IS NULL""").fetchone()[0]
        # A path used by kept metadata must never be staged, even with malformed
        # cross-category references. Queries use indexes where available.
        for file in plan["files"]:
            absolute = "/media/frigate/" + file["path"]
            for table, column in (
                ("recordings", "path"),
                ("previews", "path"),
                ("reviewsegment", "thumb_path"),
                ("export", "video_path"),
                ("export", "thumb_path"),
            ):
                if db.execute(
                    f'''SELECT 1 FROM "{table}" WHERE "{column}"=? AND id NOT IN
                    (SELECT id FROM pick_{table}) LIMIT 1''',
                    (absolute,),
                ).fetchone():
                    raise Blocked("Selected media is also referenced by protected history")
        if not math.isfinite(plan["bytes"]):
            raise Blocked("Invalid media byte count")
        storage.validate(plan["mount"])
        return plan
    except sqlite3.Error:
        raise Blocked("Database validation/selection failed or exceeded its time budget") from None
    finally:
        db.close()


def expanded(approved, current):
    if approved["scope"] != current["scope"] or approved["mount"] != current["mount"]:
        return True
    for table, rows in current["rows"].items():
        previous = {r["id"]: r["signature"] for r in approved["rows"].get(table, [])}
        if any(previous.get(r["id"]) != r["signature"] for r in rows):
            return True
    previous_files = {f["path"]: f["identity"] for f in approved["files"]}
    return any(previous_files.get(f["path"]) != f["identity"] for f in current["files"])


def summary(plan):
    return {key: plan[key] for key in ("scope", "counts", "preserved", "bytes", "warnings")}
