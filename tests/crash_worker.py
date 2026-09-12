"""Child-process harness. Uses ONLY temporary synthetic fixtures, never shipped."""

import json
import os
import sys
from pathlib import Path

from conftest import SLUG, FakeStorage, FakeSupervisor
from fsm.integration import Installation
from fsm.jobs import Engine, JobStore


class DiskSupervisor(FakeSupervisor):
    def __init__(self, state):
        super().__init__()
        self.state_path = state

    def info(self, slug):
        self.app["state"] = json.loads(self.state_path.read_text())["state"]
        return super().info(slug)

    def lifecycle(self, slug, action):
        super().lifecycle(slug, action)
        self.state_path.write_text(json.dumps({"state": self.app["state"]}))


def engine_for(root):
    supervisor = DiskSupervisor(root / "supervisor-state.json")
    options = {"target_slug": SLUG, "database_relative_path": "frigate.db", "max_plan_items": 10000}
    installation = Installation(supervisor, root / "data", options, root / "configs")
    return Engine(
        JobStore(root / "data"), installation, FakeStorage(root / "media"), gate=lambda: True
    )


if __name__ == "__main__":
    root, phase, token, signature = sys.argv[1:5]
    engine = engine_for(Path(root))

    def crash(value):
        if value == phase:
            os._exit(77)  # No Python cleanup, DB.close(), rollback or finally runs.

    engine.hook = crash
    engine.submit(
        token,
        "admin",
        signature,
        background=False,
        kind=sys.argv[5] if len(sys.argv) > 5 else "cleanup",
    )
    raise AssertionError("Crash phase was not reached")
