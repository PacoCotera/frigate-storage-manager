import json
import sqlite3

from conftest import T, event, insert, media, recording, review
from fsm.inspection import PRESERVED_LIMIT
from test_planner import plan, selected


def find(result, view, kind, id):
    return next(
        item for item in result["inspection"][view] if item["kind"] == kind and item["id"] == id
    )


def test_selected_event_review_and_files_explain_same_snapshot(env):
    event(
        env, has_snapshot=1, thumbnail="SECRET_INLINE", data=json.dumps({"secret": "SECRET_JSON"})
    )
    media(env, "clips/front-e-old.jpg")
    review(env, events=["e-old"])
    recording(env)
    result = plan(env, inspect=True)
    entry = find(result, "selected", "event", "e-old")
    assert entry["start"] == T - 100 and entry["camera"] == "front"
    assert "before the cutoff" in entry["reason"]
    assert entry["files"] == [{"path": "clips/front-e-old.jpg", "bytes": len(b"synthetic media")}]
    assert find(result, "selected", "reviewsegment", "review-old")["files"][0]["path"].startswith(
        "clips/review/"
    )
    assert "SECRET" not in str(result["inspection"])
    assert selected(plan(env), "recordings") == selected(result, "recordings")


def test_bookmark_linked_group_and_padding_have_witnesses(env):
    event(env, "bookmark", bookmark=1)
    event(env, "linked")
    review(env, "group", ["bookmark", "linked"])
    recording(env, "padded", T - 105, T - 101)
    result = plan(env, inspect=True)
    assert "Bookmarked" in find(result, "preserved", "event", "bookmark")["reason"]
    assert find(result, "preserved", "event", "linked")["related"] == {
        "kind": "reviewsegment",
        "id": "group",
    }
    assert find(result, "preserved", "reviewsegment", "group")["related"] == {
        "kind": "event",
        "id": "bookmark",
    }
    kept = find(result, "preserved", "recordings", "padded")
    assert "padding" in kept["reason"] and kept["related"]["id"] == "bookmark"


def test_active_cutoff_export_and_kept_recording_reasons(env):
    event(env, "active", end=None)
    recording(env, "boundary", end=T)
    insert(
        env,
        "previews",
        id="preview",
        start_time=T - 200,
        end_time=T - 110,
        path=media(env, "clips/previews/preview.mp4"),
    )
    recording(env, "long", T - 300, T)
    insert(env, "export", id="busy", camera="side", in_progress=1)
    recording(env, "source", camera="side")
    result = plan(env, cameras=["front", "side"], inspect=True)
    assert "Unfinished" in find(result, "preserved", "event", "active")["reason"]
    assert "cutoff" in find(result, "preserved", "recordings", "boundary")["reason"]
    assert find(result, "preserved", "recordings", "source")["related"] == {
        "kind": "export",
        "id": "busy",
    }
    assert find(result, "preserved", "previews", "preview")["related"]["kind"] == "recordings"


def test_preserved_sample_is_bounded_prioritizes_bookmark_and_states_total(env):
    event(env, "base", start=T, end=T + 1)
    with sqlite3.connect(env.db) as db:
        columns = [row[1] for row in db.execute("PRAGMA table_info(event)")]
        projection = ",".join("?" if c == "id" else f'"{c}"' for c in columns)
        db.executemany(
            f"INSERT INTO event SELECT {projection} FROM event WHERE id='base'",
            ((f"e-{n}",) for n in range(PRESERVED_LIMIT + 1)),
        )
    event(env, "late-bookmark", start=T + 100, end=T + 101, bookmark=1)
    result = plan(env, inspect=True)
    sample = result["inspection"]["preserved_samples"]["event"]
    assert sample == {"shown": PRESERVED_LIMIT, "total": PRESERVED_LIMIT + 3}
    assert result["inspection"]["preserved"][0]["id"] == "late-bookmark"
    assert len(result["inspection"]["preserved"]) == PRESERVED_LIMIT


def test_related_metadata_and_semantic_entries_are_inspectable(env):
    from fsm.database import VECTORS, load_vectors

    event(env)
    review(env, events=["e-old"])
    insert(env, "timeline", source_id="e-old")
    insert(env, "userreviewstatus", id=17, review_segment_id="review-old", user_id="private-user")
    with sqlite3.connect(env.db) as db:
        load_vectors(db)
        for table, column in VECTORS.items():
            db.execute(
                f"CREATE VIRTUAL TABLE {table} USING vec0(id TEXT PRIMARY KEY,{column} float[768] distance_metric=cosine)"
            )
            db.execute(f"INSERT INTO {table} VALUES (?,?)", ("e-old", json.dumps([1.0] * 768)))
    result = plan(env, inspect=True)
    assert find(result, "selected", "vec_thumbnails", "e-old")["related"]["id"] == "e-old"
    assert find(result, "selected", "userreviewstatus", "17")["related"]["id"] == "review-old"
    assert "private-user" not in str(result["inspection"])


def test_trigger_reason_does_not_expose_configuration(env):
    event(env)
    insert(env, "trigger", camera="front", name="SECRET", triggering_event_id="e-old")
    result = plan(env, inspect=True)
    assert "trigger" in find(result, "preserved", "event", "e-old")["reason"]
    assert "SECRET" not in str(result["inspection"])
