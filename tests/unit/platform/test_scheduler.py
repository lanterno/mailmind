"""mailmind.platform.workers.scheduler: the background jobs' timetable, one lane per job."""
import json
import threading
import time

import pytest

from mailmind.platform.workers.scheduler import Scheduler
from mailmind.bootstrap import background


def wait_until(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.005)
    return True


@pytest.fixture
def jobs():
    s = Scheduler(clock=lambda: 1_800_000_000.0)
    lanes = []

    def spawn(target):
        t = threading.Thread(target=target, daemon=True)
        lanes.append(t)
        t.start()
    s.spawn = spawn
    yield s
    s.stop()
    for t in lanes:
        t.join(5)
    assert not any(t.is_alive() for t in lanes), "a lane outlived stop()"


def test_a_job_waits_for_its_first_round_then_runs_at_its_interval(jobs):
    times = []
    jobs.add("tick", lambda: times.append(time.monotonic()), every=0.05, first=0.2)
    began = time.monotonic()
    jobs.start(jobs.spawn)
    assert wait_until(lambda: len(times) >= 3)
    assert times[0] - began >= 0.19
    assert all(b - a >= 0.045 for a, b in zip(times, times[1:]))


def test_a_round_may_say_when_the_next_one_is_due(jobs):
    times = []

    def backlog():
        times.append(time.monotonic())
        return 0 if len(times) < 3 else 60                       # brisk through a backlog, then idle
    jobs.add("backfill", backlog, every=0.5)
    jobs.start(jobs.spawn)
    assert wait_until(lambda: len(times) == 3)
    assert times[2] - times[0] < 0.4                             # well inside one interval
    time.sleep(0.6)
    assert len(times) == 3
    assert jobs.snapshot()[0]["next_at"] == 1_800_000_060.0


def test_a_round_that_raises_is_counted_and_the_job_goes_on(jobs, capsys):
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionResetError("the server said: no, ada@example.test")
    jobs.add("sync", flaky, every=0.02)
    jobs.start(jobs.spawn)
    assert wait_until(lambda: len(calls) >= 2)
    jobs.stop()
    [snap] = jobs.snapshot()
    assert snap["failures"] == 1 and snap["runs"] >= 2
    assert snap["last_error"] is None                            # the last round went well
    assert "[jobs] sync failed: ConnectionResetError" in capsys.readouterr().out


def test_the_snapshot_keeps_only_the_errors_class_name():
    s = Scheduler(clock=lambda: 1_800_000_000.0)

    def fails():
        raise RuntimeError("SELECT failed for ada@example.test")
    s.add("reconcile", fails, every=1800, first=120)
    assert s.run("reconcile") == 1800
    [snap] = s.snapshot()
    assert snap == {"name": "reconcile", "every": 1800.0, "runs": 1, "failures": 1, "running": False,
                    "last_started": 1_800_000_000.0, "last_took": snap["last_took"],
                    "last_error": "RuntimeError", "next_at": None}


def test_a_kick_runs_the_next_round_now(jobs):
    ran = threading.Event()
    jobs.add("sync", ran.set, every=300, first=300)
    jobs.start(jobs.spawn)
    time.sleep(0.05)
    assert not ran.is_set()
    jobs.kick("sync")
    assert ran.wait(5)


def test_rounds_of_one_job_never_overlap(jobs):
    inside, overlapped = [], []
    release = threading.Event()

    def slow():
        if inside:
            overlapped.append(1)
        inside.append(1)
        release.wait(5)
        inside.pop()
    jobs.add("slow", slow, every=0.01)
    jobs.start(jobs.spawn)
    assert wait_until(lambda: inside)                            # the lane's round is under way
    other = threading.Thread(target=lambda: jobs.run("slow"), daemon=True)
    other.start()                                                # a caller's round waits its turn
    time.sleep(0.05)
    release.set()
    other.join(5)
    assert overlapped == []


def test_two_jobs_cannot_share_a_name():
    s = Scheduler()
    s.add("sync", lambda: None, every=300)
    with pytest.raises(ValueError, match="already scheduled"):
        s.add("sync", lambda: None, every=60)
    assert s.names() == ["sync"]


def test_each_lane_is_named_for_its_job():
    s = Scheduler()
    s.add("sync", lambda: None, every=300)
    s.add("snooze", lambda: None, every=60, first=60)
    lanes = []
    s.start(lanes.append)
    assert [t.__name__ for t in lanes] == ["job:sync", "job:snooze"]


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
