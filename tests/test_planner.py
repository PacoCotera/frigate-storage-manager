import hashlib
import json
import sqlite3
import time
import tracemalloc

import pytest
from conftest import CUTOFF, T, event, insert, media, recording, review
from fsm.database import connect, load_vectors, supported_version, validate_schema
from fsm.planner import build_plan, camera_names
from fsm.safety import Blocked, cutoff_utc


def plan(env, **kwargs):
    return build_plan(
        env.db,
        env.version,
        env.storage,
        env.config,
        kwargs.pop("cameras", ["front"]),
        kwargs.pop("cutoff", CUTOFF),
        **kwargs,
    )


def selected(result, table):
    return {r["id"] for r in result["rows"][table]}


def test_read_only_preview_age_boundary_and_no_lifecycle(env):
    recording(env)
    recording(env, "at", T - 1, T)
    recording(env, "cross", T - 1, T + 1)
    recording(env, "other-camera", camera="side")
    before = hashlib.sha256(env.db.read_bytes()).hexdigest()
    calls = list(env.supervisor.calls)
    result = plan(env)
    assert selected(result, "recordings") == {"r-old"}
    assert result["bytes"] == len(b"synthetic media")
    assert hashlib.sha256(env.db.read_bytes()).hexdigest() == before
    assert env.supervisor.calls == calls
    assert (env.root / "recordings/r-old.mp4").exists()


def test_timezone_equivalence_and_invalid_cutoffs():
    assert cutoff_utc("2026-01-01T18:00:00-06:00") == cutoff_utc(CUTOFF)
    for bad in ("2026-01-01T18:00", "garbage", "2999-01-01T00:00Z", None):
        with pytest.raises(Blocked):
            cutoff_utc(bad)


def test_historical_and_unknown_cameras(env):
    recording(env, camera="retired")
    with connect(env.db) as db:
        assert "retired" in camera_names(db, env.config)
    assert selected(plan(env, cameras=["retired"]), "recordings") == {"r-old"}
    with pytest.raises(Blocked, match="Unknown camera"):
        plan(env, cameras=["unknown"])


def test_bookmark_transitive_groups_keep_history_and_padded_footage(env):
    event(env, "bookmark", bookmark=1)
    event(env, "linked")
    event(env, "twice-linked")
    review(env, "group-1", ["bookmark", "linked"])
    review(env, "group-2", ["linked", "twice-linked"])
    recording(env, "pre", T - 105, T - 101)
    recording(env, "post", T - 89, T - 85)
    recording(env, "safe", T - 130, T - 120)
    result = plan(env)
    assert not selected(result, "event")
    assert not selected(result, "reviewsegment")
    assert selected(result, "recordings") == {"safe"}
    assert result["preserved"]["bookmarks"] == 1


@pytest.mark.parametrize("kind", ["event", "review"])
def test_active_items_and_cutoff_preserve_overlaps(env, kind):
    (event if kind == "event" else review)(env, end=None)
    recording(env)
    recording(env, "very-old", T - 200, T - 190)
    assert selected(plan(env), "recordings") == {"very-old"}


def test_configured_large_capture_and_historical_global_fallback(env):
    for config in (env.config["cameras"]["front"], env.config["global_capture"]):
        config["alerts_pre_capture"] = 60
        config["detections_post_capture"] = 60
    event(env, bookmark=1)
    recording(env, "padded", T - 160, T - 150)
    assert not selected(plan(env), "recordings")


def test_preview_kept_when_recording_survives(env):
    recording(env, end=T)
    insert(
        env,
        "previews",
        id="p",
        start_time=T - 200,
        end_time=T - 1,
        path=media(env, "clips/previews/p.mp4"),
    )
    assert not selected(plan(env), "previews")


def test_exports_default_creation_cutoff_and_in_progress_preservation(env):
    for id, in_progress, date in (("old", 0, T - 1), ("new", 0, T + 1), ("busy", 1, T - 1)):
        insert(
            env,
            "export",
            id=id,
            camera="side",
            in_progress=in_progress,
            date=date,
            video_path=media(env, f"exports/{id}.mp4"),
            thumb_path=media(env, f"clips/export/{id}.webp"),
        )
    recording(env, camera="side")
    event(env, camera="side")
    assert not selected(plan(env, cameras=["side"]), "export")
    result = plan(env, cameras=["side"], include_exports=True)
    assert selected(result, "export") == {"old"}
    assert not selected(result, "recordings")
    assert not selected(result, "event")


def test_related_metadata_images_and_vectors(env):
    event(env, has_snapshot=1, thumbnail=None)
    media(env, "clips/front-e-old.jpg")
    media(env, "clips/front-e-old-clean.webp")
    media(env, "clips/front-e-old-clean.png")
    media(env, "clips/thumbs/front/e-old.webp")
    review(env, events=["e-old"])
    insert(env, "timeline", source_id="e-old")
    insert(env, "userreviewstatus", user_id="u", review_segment_id="review-old")
    with sqlite3.connect(env.db) as db:
        load_vectors(db)
        for table, col in (
            ("vec_thumbnails", "thumbnail_embedding"),
            ("vec_descriptions", "description_embedding"),
        ):
            db.execute(
                f"CREATE VIRTUAL TABLE {table} USING vec0(id TEXT PRIMARY KEY,{col} FLOAT[768] distance_metric=cosine)"
            )
            db.execute(f"INSERT INTO {table} VALUES (?,?)", ("e-old", json.dumps([0.1] * 768)))
    result = plan(env)
    assert result["counts"]["timeline"] == result["counts"]["userreviewstatus"] == 1
    assert result["counts"]["snapshots"] == 3
    assert selected(result, "vec_thumbnails") == {"e-old"}
    assert selected(result, "vec_descriptions") == {"e-old"}


def test_trigger_reference_preserves_event_and_configuration(env):
    event(env)
    insert(env, "trigger", camera="front", name="gate", triggering_event_id="e-old")
    assert not selected(plan(env), "event")


def test_missing_indexed_media_and_unavailable_mount_block(env):
    recording(env)
    (env.root / "recordings/r-old.mp4").unlink()
    with pytest.raises(Blocked, match="missing"):
        plan(env)
    env.storage.unavailable = True
    with pytest.raises(Blocked, match="unavailable"):
        plan(env)


@pytest.mark.parametrize("version", ["0.17.3", "0.17.20", "0.18.0", "0.17.2evil", "v0.17.2"])
def test_unsupported_versions(version):
    with pytest.raises(Blocked):
        supported_version(version)


@pytest.mark.parametrize(
    "mutation",
    [
        "ALTER TABLE event ADD COLUMN mystery TEXT",
        "CREATE TABLE unknown (id TEXT)",
        "CREATE TRIGGER bad AFTER DELETE ON event BEGIN DELETE FROM user; END",
        "DROP TABLE userreviewstatus",
    ],
)
def test_unknown_schema_blocks(env, mutation):
    with sqlite3.connect(env.db) as db:
        db.execute(mutation)
    with connect(env.db) as db, pytest.raises(Blocked):
        validate_schema(db, env.version)


def test_invalid_review_relationships_block(env):
    review(env)
    with sqlite3.connect(env.db) as db:
        db.execute("UPDATE reviewsegment SET data='{}'")
    with pytest.raises(Blocked, match="relationships"):
        plan(env)


@pytest.mark.parametrize("inspect", [False, True])
def test_large_archive_bounded_memory_and_plan_cap(env, inspect):
    recording(env)
    with sqlite3.connect(env.db) as db:
        db.executemany(
            """INSERT INTO recordings (id,camera,path,start_time,end_time,duration,segment_size)
            VALUES (?,'side',?,?,?,10,1)""",
            (
                (f"archive-{i}", f"/media/frigate/recordings/archive-{i}.mp4", T + i, T + i + 10)
                for i in range(100000)
            ),
        )
    tracemalloc.start()
    start = time.monotonic()
    result = plan(env, cameras=["front", "side"], inspect=inspect)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert selected(result, "recordings") == {"r-old"}
    assert peak < 12 * 1024 * 1024
    assert time.monotonic() - start < 20
    if inspect:
        assert result["inspection"]["preserved_samples"]["recordings"] == {
            "shown": 500,
            "total": 100000,
        }
    with pytest.raises(Blocked, match="limit"):
        plan(env, max_items=1)
