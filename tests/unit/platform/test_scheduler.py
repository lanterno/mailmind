"""mailmind.platform.workers.scheduler: the background jobs' timetable, one lane per job.

The scheduler's clock is fixed at T, so a lane's `next_at` says how long it waits before its next round; the tests read that
rather than time how long the lane took."""
import threading

import pytest

from mailmind.platform.workers.scheduler import Scheduler
from waiting import blocked_in, wait_until


T = 1_800_000_000.0


def due(jobs):
    return jobs.snapshot()[0]["next_at"]


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


def test_a_job_waits_for_its_first_round_then_runs_at_its_interval(jobs):
    rounds = []
    jobs.add("tick", lambda: rounds.append(1), every=60, first=300)
    jobs.start(jobs.spawn)
    assert wait_until(lambda: due(jobs) == T + 300)              # the lane waits out `first`
    assert rounds == []
    jobs.kick("tick")
    assert wait_until(lambda: rounds == [1] and due(jobs) == T + 60)     # then `every`, after each round


def test_a_round_may_say_when_the_next_one_is_due(jobs):
    rounds = []

    def backlog():
        rounds.append(1)
        return 0 if len(rounds) < 3 else 60                      # brisk through a backlog, then idle
    jobs.add("backfill", backlog, every=3600)
    jobs.start(jobs.spawn)
    assert wait_until(lambda: due(jobs) == T + 60)               # three rounds with no wait between, not an hour
    assert len(rounds) == 3 and jobs.snapshot()[0]["runs"] == 3


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
    assert wait_until(lambda: due(jobs) == T + 300)              # the lane is waiting out its first 300 s
    assert not ran.is_set()
    jobs.kick("sync")
    assert ran.wait(30)


def test_rounds_of_one_job_never_overlap(jobs):
    inside, overlapped = [], []
    release = threading.Event()

    def slow():
        if inside:
            overlapped.append(1)
        inside.append(1)
        release.wait(30)
        inside.pop()
    jobs.add("slow", slow, every=300)
    jobs.start(jobs.spawn)
    assert wait_until(lambda: inside)                            # the lane's round is under way
    other = threading.Thread(target=lambda: jobs.run("slow"), daemon=True)
    other.start()
    assert wait_until(lambda: blocked_in(other, Scheduler.run))  # a caller's round waits its turn
    assert len(inside) == 1
    release.set()
    other.join(30)
    assert overlapped == [] and jobs.snapshot()[0]["runs"] == 2


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
