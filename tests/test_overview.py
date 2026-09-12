import pytest
from conftest import T, event, recording, review
from fsm.overview import coverage
from fsm.previews import PreviewService
from fsm.safety import Blocked
from test_planner import plan
from test_previews import complete, request


def test_coverage_excludes_gaps_overlaps_and_nested_intervals():
    assert coverage([(0, 10), (5, 15), (20, 30), (21, 22)]) == 25
    assert coverage([]) == 0


def test_camera_bytes_hours_and_duration_match_selected_files(env):
    recording(env, "one", T - 7200, T - 7190)
    recording(env, "overlap", T - 7195, T - 7180)
    recording(env, "gap", T - 7170, T - 7160)
    recording(env, "other-hour", T - 3600, T - 3590)
    recording(env, "other-camera", T - 7200, T - 7190, camera="side")
    result = plan(env, cameras=["front", "side"], inspect=True)
    overview = result["inspection"]["overview"]
    front, side = overview["cameras"]
    assert front["recording_seconds"] == 40
    assert side["recording_seconds"] == 10
    assert overview["recording_seconds"] == 50
    assert sum(c["bytes"] for c in overview["cameras"]) == result["bytes"]
    groups = result["inspection"]["hours"]
    assert len(groups) == 3
    assert groups[0]["recording_seconds"] == 30
    assert (
        groups[0]["end"] - groups[0]["start"] == 40
    )  # Never equate range span with actual footage.
    assert sum(g["bytes"] for g in groups) == result["bytes"]


def test_cross_hour_overlap_is_counted_once_in_camera_total(env):
    recording(env, "cross", T - 3605, T - 3590)
    recording(env, "next", T - 3600, T - 3580)
    result = plan(env, inspect=True)["inspection"]
    assert len(result["hours"]) == 2
    assert result["overview"]["recording_seconds"] == 25
    assert sum(h["recording_seconds"] for h in result["hours"]) == 35


def test_preserved_totals_cover_history_beyond_diagnostic_sample(env):
    for i in range(40):
        event(env, id=f"recent-{i}", start=T + 1, end=T + 10)
    event(env, "bookmark", bookmark=1)
    review(env, events=["bookmark"])
    recording(env, "protected")
    result = plan(env, inspect=True)
    camera = result["inspection"]["overview"]["cameras"][0]
    assert camera["kept"]["event"] == 41
    assert camera["reasons"]["recent"] == 40
    assert camera["reasons"]["bookmarks"] == 1
    assert camera["reasons"]["linked_history"] == 1
    assert camera["reasons"]["required_footage"] == 1
    assert result["inspection"]["preserved_samples"]["event"]["shown"] == 25
    assert camera["bytes"] == 0 and camera["recording_seconds"] == 0


def test_hour_pages_are_owner_bound_expiring_and_from_saved_snapshot(env):
    for i in range(13):
        recording(env, id=f"hour-{i}", start=T - (i + 1) * 3600, end=T - (i + 1) * 3600 + 10)
    service = PreviewService(env.installation, env.storage, env.store)
    task = complete(service, service.submit(request(), "admin"))
    assert len(service.hours(task["id"], "admin", "front", 0)["items"]) == 12
    assert len(service.hours(task["id"], "admin", "front", 1)["items"]) == 1
    assert task["result"]["overview"]["recording_seconds"] == 130
    assert all(v >= 0 for v in task["timings"].values())
    assert "building_details" in task["timings"]
    with pytest.raises(Blocked):
        service.hours(task["id"], "other", "front", 0)
    with env.store.connect() as db:
        db.execute("UPDATE previews SET created=0")
    with pytest.raises(Blocked, match="expired"):
        service.hours(task["id"], "admin", "front", 0)
