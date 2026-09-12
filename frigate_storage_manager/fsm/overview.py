"""Camera summaries and compact recording time windows from the frozen selection."""

from collections import defaultdict
from math import floor

HISTORY = ("event", "reviewsegment", "recordings", "previews", "export")


def coverage(intervals):
    """Union duration: gaps stay gaps and overlapping recordings count once."""
    seconds, end = 0.0, float("-inf")
    for start, stop in sorted(intervals):
        seconds += max(0, stop - max(start, end))
        end = max(end, stop)
    return seconds


def overview(db, plan, selected):
    cameras = {
        name: {
            "camera": name,
            "bytes": 0,
            "selected": {},
            "kept": {},
            "reasons": {},
            "start": None,
            "end": None,
            "recording_seconds": 0,
        }
        for name in plan["scope"]["cameras"]
    }
    intervals, groups = defaultdict(list), {}
    for entry in selected:
        camera = cameras.get(entry["camera"])
        if camera is None:
            continue  # User status/vector entries have no camera; totals remain in technical details.
        kind = entry["kind"]
        camera["selected"][kind] = camera["selected"].get(kind, 0) + 1
        size = sum(file["bytes"] for file in entry["files"])
        camera["bytes"] += size
        if kind != "recordings":
            continue
        start, end = entry["start"], entry["end"]
        interval = (start, end)
        intervals[entry["camera"]].append(interval)
        camera["start"] = start if camera["start"] is None else min(camera["start"], start)
        camera["end"] = end if camera["end"] is None else max(camera["end"], end)
        key = (entry["camera"], floor(start / 3600) * 3600)
        group = groups.setdefault(
            key,
            {
                "camera": key[0],
                "hour": key[1],
                "start": start,
                "end": end,
                "files": 0,
                "bytes": 0,
                "intervals": [],
            },
        )
        group["start"], group["end"] = min(group["start"], start), max(group["end"], end)
        group["files"] += len(entry["files"])
        group["bytes"] += size
        group["intervals"].append(interval)
    for name, camera in cameras.items():
        camera["recording_seconds"] = coverage(intervals[name])
    hours = []
    for key in sorted(groups):
        group = groups[key]
        group["recording_seconds"] = coverage(group.pop("intervals"))
        hours.append(group)
    # Exact aggregate counts across ALL kept history, not the diagnostic sample.
    # Classification uses the successful planner's excluded sets. These are broad
    # reasons; individual witnesses are only sought for the small detail sample.
    for table in HISTORY:
        if table == "export":
            case = "CASE WHEN in_progress!=0 THEN 'unfinished' ELSE 'exports' END"
            params = ()
        else:
            bookmark = "WHEN retain_indefinitely!=0 THEN 'bookmarks'" if table == "event" else ""
            protected = (
                "linked_history" if table in ("event", "reviewsegment") else "required_footage"
            )
            case = f"CASE {bookmark} WHEN end_time IS NULL THEN 'unfinished' WHEN end_time>=? THEN 'recent' ELSE '{protected}' END"
            params = (plan["scope"]["cutoff"],)
        for row in db.execute(
            f'''SELECT camera,{case} AS reason,count(*) AS n FROM "{table}"
            JOIN selected_camera USING(camera) WHERE id NOT IN (SELECT id FROM pick_{table})
            GROUP BY camera,reason''',
            params,
        ):
            camera = cameras[row["camera"]]
            camera["kept"][table] = camera["kept"].get(table, 0) + row["n"]
            key = row["reason"]
            camera["reasons"][key] = camera["reasons"].get(key, 0) + row["n"]
    return {
        "cameras": list(cameras.values()),
        "recording_seconds": sum(c["recording_seconds"] for c in cameras.values()),
    }, hours
