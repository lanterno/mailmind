"""mailmind.platform.workers.gates: the gate each account's live-IMAP work passes."""
import threading

import pytest

from mailmind.platform.workers import gates

ADA = "ada@example.test"
NOW = gates.Priority.INTERACTIVE


def holding(account=gates.EVERY_ACCOUNT):
    """Hold `account`'s gate in another thread, as a long mail operation would. Returns the
    thread and the event that lets it go."""
    started, release = threading.Event(), threading.Event()

    def job():
        started.set()
        release.wait(10)
    t = threading.Thread(target=lambda: gates.run(account, job, priority=NOW), daemon=True)
    t.start()
    assert started.wait(5)
    return t, release


def test_a_job_runs_with_its_gate_held_and_gives_back_what_it_returns():
    assert not gates.held(ADA)
    assert gates.run(ADA, lambda: ("held", gates.held(ADA)), priority=NOW) == ("held", True)
    assert not gates.held(ADA)


def test_the_gate_comes_free_when_the_job_raises():
    def fails():
        raise ValueError("the server said NO")
    with pytest.raises(ValueError, match="said NO"):
        gates.run(ADA, fails, priority=NOW)
    assert not gates.held(ADA)


def test_a_job_that_mustnt_wait_finds_a_held_gate_busy_and_doesnt_run():
    t, release = holding()
    ran = []
    try:
        with pytest.raises(gates.Busy):
            gates.run(ADA, lambda: ran.append(1), priority=gates.Priority.MAINTENANCE, wait=False)
    finally:
        release.set()
        t.join(5)
    assert ran == []
    assert gates.run(ADA, lambda: "free now", priority=gates.Priority.MAINTENANCE, wait=False) == "free now"


def test_a_job_waits_for_a_held_gate_and_then_runs():
    t, release = holding(ADA)
    out = []
    waiter = threading.Thread(target=lambda: out.append(gates.run(ADA, lambda: "ran", priority=NOW)), daemon=True)
    waiter.start()
    waiter.join(0.3)
    assert waiter.is_alive() and out == []
    release.set()
    waiter.join(5)
    t.join(5)
    assert out == ["ran"]


def test_a_job_that_waits_on_a_gate_from_inside_a_job_raises_instead_of_hanging():
    def nested():
        return gates.run(ADA, lambda: "never", priority=NOW)
    with pytest.raises(RuntimeError, match="from inside a job"):
        gates.run(ADA, nested, priority=NOW)
    assert not gates.held(ADA)


def test_a_job_inside_a_job_that_mustnt_wait_finds_the_gate_busy():
    """As the background loops' non-blocking take of the lock did: skip, never hang."""
    def nested():
        with pytest.raises(gates.Busy):
            gates.run(ADA, lambda: "never", priority=gates.Priority.MAINTENANCE, wait=False)
        return "skipped"
    assert gates.run(ADA, nested, priority=NOW) == "skipped"


def test_every_job_says_how_urgent_it_is():
    with pytest.raises(TypeError):
        gates.run(ADA, lambda: None)                      # the priority is required
    with pytest.raises(ValueError):
        gates.run(ADA, lambda: None, priority=7)
    assert [p.name for p in gates.Priority] == ["INTERACTIVE", "AFTER_USER", "SYNC", "MAINTENANCE"]
