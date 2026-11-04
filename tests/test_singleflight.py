"""SingleFlight: one run at a time for each key, and none for what is already stored."""
import threading

import pytest

from mailmind.platform.workers.singleflight import SingleFlight


class Store:
    """A cache the runs fill: read() is what it holds for the key, or None."""

    def __init__(self, value=None):
        self.value = value
        self.reads = 0

    def read(self):
        self.reads += 1
        return self.value


def held_run(flight, key, store, answer="made"):
    """Start a run that leads and holds until released. Returns (release, thread, results)."""
    started, release, out = threading.Event(), threading.Event(), []

    def make():
        started.set()
        release.wait(5)
        store.value = answer
        return answer
    t = threading.Thread(target=lambda: out.append(flight.run(key, make, store.read, wait=5)))
    t.start()
    assert started.wait(5)
    return release, t, out


def test_a_caller_that_finds_a_run_going_waits_and_reads_what_it_stored():
    flight, store, made = SingleFlight(), Store(), []
    release, leader, led = held_run(flight, "brief", store)
    follower_out = []
    follower = threading.Thread(target=lambda: follower_out.append(
        flight.run("brief", lambda: made.append(1), store.read, wait=5)))
    follower.start()
    release.set()
    leader.join(5)
    follower.join(5)
    assert (led, follower_out, made) == (["made"], ["made"], [])


def test_the_leader_reads_again_before_it_makes():
    flight, store, made = SingleFlight(), Store("stored meanwhile"), []
    assert flight.run("brief", lambda: made.append(1) or "made", store.read, wait=5) == "stored meanwhile"
    assert made == []


def test_a_leader_asked_to_make_afresh_skips_that_read():
    flight, store = SingleFlight(), Store("old")
    assert flight.run("brief", lambda: "new", store.read, wait=5, recheck=False) == "new"
    assert store.reads == 0


def test_a_follower_of_a_run_that_stored_nothing_gets_none():
    flight, store = SingleFlight(), Store()
    started, release = threading.Event(), threading.Event()

    def failing():
        started.set()
        release.wait(5)
        return "failed, nothing stored"
    leader = threading.Thread(target=lambda: flight.run("brief", failing, store.read, wait=5))
    leader.start()
    assert started.wait(5)
    out = []
    follower = threading.Thread(target=lambda: out.append(flight.run("brief", lambda: 1 / 0, store.read, wait=5)))
    follower.start()
    release.set()
    leader.join(5)
    follower.join(5)
    assert out == [None]


def test_a_follower_waits_no_longer_than_it_was_told():
    flight, store = SingleFlight(), Store()
    release, leader, _ = held_run(flight, "brief", store)
    try:
        assert flight.run("brief", lambda: 1 / 0, store.read, wait=0.05) is None    # still going
    finally:
        release.set()
        leader.join(5)


def test_runs_for_different_keys_dont_wait_for_each_other():
    flight, store = SingleFlight(), Store()
    release, leader, _ = held_run(flight, "brief", store)
    try:
        assert flight.run("review", lambda: "review made", Store().read, wait=5) == "review made"
    finally:
        release.set()
        leader.join(5)


def test_busy_says_whether_a_run_is_going():
    flight, store = SingleFlight(), Store()
    assert not flight.busy("brief")
    release, leader, _ = held_run(flight, "brief", store)
    assert flight.busy("brief") and not flight.busy("review")
    release.set()
    leader.join(5)
    assert not flight.busy("brief")


def test_a_make_that_raises_frees_the_key_and_raises_in_the_leader():
    flight, store = SingleFlight(), Store()
    with pytest.raises(ZeroDivisionError):
        flight.run("brief", lambda: 1 / 0, store.read, wait=5)
    assert not flight.busy("brief")
    assert flight.run("brief", lambda: "made", store.read, wait=5) == "made"
