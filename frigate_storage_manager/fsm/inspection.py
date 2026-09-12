"""Bounded, metadata-only explanations from the planner's own read snapshot."""

from .database import VECTORS

HISTORY = ("event", "reviewsegment", "recordings", "previews", "export")
PRESERVED_LIMIT = 500  # Per category; never copy a whole long-retention archive.


def text(value):
    return str(value)[:256] if value is not None else ""


def related(kind, row):
    return {"kind": kind, "id": text(row[0])} if row else None


def reason(db, table, row, cutoff, include_exports):
    camera = row["camera"]
    if table == "event" and row["retain_indefinitely"]:
        return "Bookmarked event; its linked history and required footage are preserved.", None
    if table == "export":
        if row["in_progress"]:
            return "Export is in progress; its camera's source history is preserved.", None
        if not include_exports:
            return "Completed exports were excluded from this preview.", None
        return "Export creation time is not eligible for this cutoff.", None
    if row["end_time"] is None:
        return "Unfinished item; completion time is not yet known.", None
    if row["end_time"] >= cutoff:
        return "Ends at or after the cutoff, including items crossing the boundary.", None
    export = db.execute(
        "SELECT id FROM export WHERE camera=? AND in_progress!=0 LIMIT 1", (camera,)
    ).fetchone()
    if export:
        return "Source history is needed by an in-progress export.", related("export", export)
    if table == "event":
        trigger = db.execute(
            "SELECT triggering_event_id FROM trigger WHERE triggering_event_id=? LIMIT 1",
            (row["id"],),
        ).fetchone()
        if trigger:
            return "Referenced by a configured trigger; trigger history is preserved.", None
        link = db.execute(
            """SELECT review FROM links WHERE event=? AND review NOT IN
            (SELECT id FROM pick_reviewsegment) LIMIT 1""",
            (row["id"],),
        ).fetchone()
        if link:
            return "Belongs to a preserved review/event group.", related("reviewsegment", link)
    elif table == "reviewsegment":
        link = db.execute(
            """SELECT l.event FROM links l JOIN event e ON e.id=l.event
            WHERE l.review=? AND e.id NOT IN (SELECT id FROM pick_event)
            ORDER BY e.retain_indefinitely DESC LIMIT 1""",
            (row["id"],),
        ).fetchone()
        if link:
            return "Linked to a preserved event; the whole review/event group is kept.", related(
                "event", link
            )
    else:
        cap = db.execute(
            "SELECT pre,post FROM selected_camera WHERE camera=?", (camera,)
        ).fetchone()
        for kind in ("event", "reviewsegment"):
            order = "ORDER BY retain_indefinitely DESC" if kind == "event" else ""
            witness = db.execute(
                f'''SELECT id FROM "{kind}" WHERE camera=?
                AND id NOT IN (SELECT id FROM pick_{kind}) AND start_time-?<=?
                AND coalesce(end_time+?,1e30)>=? {order} LIMIT 1''',
                (camera, cap[0], row["end_time"], cap[1], row["start_time"]),
            ).fetchone()
            if witness:
                return (
                    "Overlaps preserved history, including its pre/post capture padding.",
                    related(kind, witness),
                )
        if table == "previews":
            witness = db.execute(
                """SELECT id FROM recordings WHERE camera=?
                AND id NOT IN (SELECT id FROM pick_recordings)
                AND start_time<=? AND end_time>=? LIMIT 1""",
                (camera, row["end_time"], row["start_time"]),
            ).fetchone()
            if witness:
                return "Overlaps a preserved recording segment.", related("recordings", witness)
    return "Not eligible under the verified selection rules; no narrower reason is available.", None


def item(table, row, explanation, witness=None):
    values = dict(row)
    return {
        "kind": table,
        "id": text(values.get("_key", values.get("id"))),
        "camera": text(values.get("camera")),
        "start": values.get("start_time", values.get("date", values.get("timestamp"))),
        "end": values.get("end_time"),
        "label": text(values.get("label", values.get("severity"))),
        "reason": explanation,
        "related": witness,
    }


def inspect_plan(db, plan, tables, progress):
    """All selected records, plus a labelled sample of preserved primary history.

    Only allowlisted fields are copied: never thumbnails, vector contents, event
    data JSON, users, trigger configuration, video bytes or credentials.
    """
    selected, preserved, samples = [], [], {}
    files = {f["path"]: f for f in plan["files"]}
    for table in (*tables, *plan["vectors"]):
        progress("building_details", category=table)
        key = "rowid" if table == "timeline" else "id"
        # Do not SELECT * here: vector/thumbnail payloads can be very large.
        columns = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
        allowed = [
            c
            for c in (
                "camera",
                "start_time",
                "end_time",
                "date",
                "timestamp",
                "label",
                "severity",
                "source_id",
                "review_segment_id",
                "path",
                "thumb_path",
                "video_path",
            )
            if c in columns
        ]
        projection = ",".join([f"{key} AS _key", *[f'"{c}"' for c in allowed]])
        # Vector IDs were verified by the planner. Read its small temp selection,
        # avoiding sqlite-vec 0.1.3 subquery scans and embedding materialization.
        source = f"pick_{table}" if table in VECTORS else table
        for row in db.execute(
            f'SELECT {projection} FROM "{source}" WHERE {key} IN (SELECT id FROM pick_{table}) ORDER BY {key}'
        ):
            witness = None
            if table in VECTORS:
                explanation = "Semantic reference associated with a selected event."
                witness = {"kind": "event", "id": text(row["_key"])}
            elif table == "timeline":
                explanation = "Timeline entry associated with a selected event."
                witness = {"kind": "event", "id": text(row["source_id"])}
            elif table == "userreviewstatus":
                explanation = "Read-status record associated with a selected review."
                witness = {"kind": "reviewsegment", "id": text(row["review_segment_id"])}
            elif table == "export":
                explanation = "Completed export created before the cutoff; exports were included."
            else:
                explanation = "Completed before the cutoff and not required by preserved history."
            entry = item(table, row, explanation, witness)
            paths = []
            if table == "event":
                name = f"{row['camera']}-{row['_key']}"
                paths = [
                    f"clips/{name}.jpg",
                    f"clips/{name}-clean.webp",
                    f"clips/{name}-clean.png",
                    f"clips/thumbs/{row['camera']}/{row['_key']}.webp",
                ]
            else:
                for column in ("path", "thumb_path", "video_path"):
                    if column in row.keys() and str(row[column]).startswith("/media/frigate/"):
                        paths.append(str(row[column])[len("/media/frigate/") :])
            entry["files"] = [
                {"path": p, "bytes": files[p]["identity"]["size"]} for p in paths if p in files
            ]
            selected.append(entry)
    for table in HISTORY:
        progress("building_details", category=table)
        columns = (
            "id,camera,date,in_progress" if table == "export" else "id,camera,start_time,end_time"
        )
        if table == "event":
            columns += ",retain_indefinitely,label"
            order = "retain_indefinitely DESC, end_time IS NULL DESC, end_time, id"
        else:
            order = "date,id" if table == "export" else "end_time IS NULL DESC,end_time,id"
        rows = db.execute(
            f'''SELECT {columns} FROM "{table}" JOIN selected_camera USING(camera)
            WHERE id NOT IN (SELECT id FROM pick_{table}) ORDER BY {order} LIMIT ?''',
            (PRESERVED_LIMIT,),
        )
        count = 0
        for row in rows:
            explanation, witness = reason(
                db, table, row, plan["scope"]["cutoff"], plan["scope"]["include_exports"]
            )
            preserved.append(item(table, row, explanation, witness))
            count += 1
        samples[table] = {"shown": count, "total": plan["preserved"][table]}
    return {
        "selected": selected,
        "preserved": preserved,
        "preserved_samples": samples,
        "preserved_limit_per_category": PRESERVED_LIMIT,
    }
