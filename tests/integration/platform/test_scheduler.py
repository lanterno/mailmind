"""mailmind.platform.workers.scheduler: the background jobs' timetable, one lane per job.

The scheduler's clock is fixed at T, so a lane's `next_at` says how long it waits before its next round; the tests read that
rather than time how long the lane took."""
import json
import threading

import pytest

from mailmind.platform.workers.scheduler import Scheduler
from mailmind.bootstrap import background


T = 1_800_000_000.0


@pytest.fixture
def jobs():
    s = Scheduler(clock=lambda: T)
    lanes = []

    def spawn(target):
        t = threading.Thread(target=target, daemon=True)
        lanes.append(t)
        t.start()
    s.spawn = spawn
    yield s
    s.stop()
    for t in lanes:
        t.join(30)
    assert not any(t.is_alive() for t in lanes), "a lane outlived stop()"


# -- what /api/debug/jobs shows ---------------------------------------------------

@pytest.mark.parametrize("named, shown", [(None, "full"), ("read-only", "read-only")])
def test_the_jobs_route_shows_the_profile_and_each_jobs_last_round(app, monkeypatch, named, shown):
    if named:
        monkeypatch.setenv("MAILMIND_PROFILE", named)
    jobs = Scheduler()

    def refused():
        raise ConnectionResetError("the server said: no, ada@example.test")
    jobs.add("sync", lambda: 30, every=300)
    jobs.add("expire", refused, every=3600, first=180)
    jobs.run("sync")
    jobs.run("expire")
    monkeypatch.setattr(background, "JOBS", jobs)

    r = app.get("/api/debug/jobs")
    assert r.status == 200
    assert (r.json["ok"], r.json["profile"]) == (True, shown)
    assert r.json["generated"] == "2026-11-02T12:00:00+00:00"
    sync, expire = r.json["jobs"]
    started = 1_793_620_800.0                                      # conftest.NOW, the frozen clock
    assert {k: sync[k] for k in ("name", "every", "runs", "failures", "running", "last_started", "last_error")} == \
        {"name": "sync", "every": 300.0, "runs": 1, "failures": 0, "running": False,
         "last_started": started, "last_error": None}
    assert {k: expire[k] for k in ("name", "runs", "failures", "last_error")} == \
        {"name": "expire", "runs": 1, "failures": 1, "last_error": "ConnectionResetError"}
    assert sync["last_took"] >= 0 and sync["next_at"] is None      # no lane runs it
    assert "example.test" not in json.dumps(r.json)                # the error's text stays in the log


def test_with_no_jobs_scheduled_the_route_shows_none(app):
    r = app.get("/api/debug/jobs")
    assert (r.status, r.json["jobs"]) == (200, [])
