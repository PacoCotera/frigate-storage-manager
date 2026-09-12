import assert from "node:assert/strict";
import test from "node:test";
import { relativeCutoff } from "../frigate_storage_manager/fsm/static/time.mjs";

const now = Date.parse("2026-09-12T18:45:30.250Z");

test("hours preserve time precision for a sub-day preview", () => {
  assert.equal(relativeCutoff("hours", "12", now).toISOString(), "2026-09-12T06:45:30.250Z");
  assert.equal(relativeCutoff("hours", "1", now).toISOString(), "2026-09-12T17:45:30.250Z");
});

test("a day and 24 hours are the same elapsed time across a DST change", () => {
  const afterSpringForward = Date.parse("2026-03-08T12:00:00-04:00");
  assert.equal(relativeCutoff("hours", "24", afterSpringForward).toISOString(), "2026-03-07T16:00:00.000Z");
  assert.equal(+relativeCutoff("days", "1", afterSpringForward), +relativeCutoff("hours", "24", afterSpringForward));
});

test("invalid relative inputs cannot silently select a different cutoff", () => {
  for (const unit of ["hours", "days"]) {
    for (const input of ["", " ", "0", "-1", "0.5", "1.5", "NaN", "Infinity", "876001"]) {
      assert.throws(() => relativeCutoff(unit, input, now), /whole number/);
    }
  }
  assert.throws(() => relativeCutoff("days", "36501", now), /whole number/);
  assert.throws(() => relativeCutoff("minutes", "1", now), /Choose days or hours/);
});

test("maximum hours and days retain the same supported range", () => {
  assert.equal(+relativeCutoff("hours", "876000", now), +relativeCutoff("days", "36500", now));
});
