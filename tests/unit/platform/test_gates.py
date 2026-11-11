"""mailmind.platform.workers.gates: the gate each account's live-IMAP work passes. What holds for
both kinds of gate runs under each; what only a gate for each account does runs under that."""
import threading
import time

import pytest

from gate import waits_for_a_gate
from mailmind.platform.workers import gates
from waiting import wait_until

ADA, BEA = "ada@example.test", "bea@example.test"
NOW = gates.Priority.INTERACTIVE


@pytest.fixture(autouse=True, params=["global", "account"])
def kind(request, monkeypatch):
    monkeypatch.setenv("MAILMIND_WORKERS", request.param)
    return request.param


@pytest.fixture
def per_account(kind):
    if kind != "account":
        pytest.skip("only a gate for each account does this")


def holding(account=gates.EVERY_ACCOUNT):
    """Hold `account`'s gate in another thread, as a long mail operation would. Returns the
    thread and the event that lets it go."""
    started, release = threading.Event(), threading.Event()

    def job():
        started.set()
        release.wait(30)
    t = threading.Thread(target=lambda: gates.run(account, job, priority=NOW), daemon=True)
    t.start()
    assert started.wait(30)
    return t, release


def queued(job):
    """Start `job` on a thread of its own, and return it once it waits for a gate: an account's, or the one lock."""
    t = threading.Thread(target=job, daemon=True)
    t.start()
    assert wait_until(lambda: waits_for_a_gate(t))
    return t


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
        t.join(30)
    assert ran == []
    assert gates.run(ADA, lambda: "free now", priority=gates.Priority.MAINTENANCE, wait=False) == "free now"


def test_a_job_waits_for_a_held_gate_and_then_runs():
    t, release = holding(ADA)
    out = []
    waiter = queued(lambda: out.append(gates.run(ADA, lambda: "ran", priority=NOW)))
    assert out == []
    release.set()
    waiter.join(30)
    t.join(30)
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


def test_with_one_gate_for_all_another_account_waits_too(kind):
    if kind != "global":
        pytest.skip("only the one lock does this")
    t, release = holding(ADA)
    try:
        with pytest.raises(gates.Busy):
            gates.run(BEA, lambda: "never", priority=NOW, wait=False)
        assert gates.held(BEA)
    finally:
        release.set()
        t.join(30)


def test_a_kind_it_doesnt_take_shares_the_one_lock_and_says_so(monkeypatch, capsys):
    from mailmind.platform import environment
    environment.forget()
    monkeypatch.setenv("MAILMIND_WORKERS", "acount")
    assert not gates.per_account() and not gates.per_account()
    assert capsys.readouterr().out == "[env] MAILMIND_WORKERS is set to a value it doesn't take, so it reads as global\n"


def test_left_unset_every_account_shares_the_one_lock(monkeypatch):
    """Which is the default until the owner has soaked a gate for each account."""
    monkeypatch.delenv("MAILMIND_WORKERS")
    assert not gates.per_account()
    t, release = holding(ADA)
    try:
        with pytest.raises(gates.Busy):
            gates.run(BEA, lambda: "never", priority=NOW, wait=False)
        assert gates.held(BEA)
    finally:
        release.set()
        t.join(30)


# ── a gate for each account ────────────────────────────────────────────────────────────────────

def test_a_held_account_holds_up_only_its_own_jobs(per_account):
    t, release = holding(ADA)
    try:
        assert gates.run(BEA, lambda: "at once", priority=NOW, wait=False) == "at once"
        assert (gates.held(ADA), gates.held(BEA), gates.held()) == (True, False, True)
        with pytest.raises(gates.Busy):
            gates.run(ADA, lambda: "never", priority=NOW, wait=False)
    finally:
        release.set()
        t.join(30)
    assert not gates.held()


def queue_behind(account, *priorities):
    """While `account`'s gate is held, start a job at each priority in turn, each waiting; then
    let the gate go and return the order they ran in."""
    t, release = holding(account)
    ran = []
    waiters = [queued(lambda p=p: gates.run(account, lambda: ran.append(p), priority=p)) for p in priorities]
    release.set()
    for w in [t, *waiters]:
        w.join(30)
    return ran


def test_waiting_jobs_go_by_priority_then_in_turn(per_account):
    P = gates.Priority
    assert queue_behind(ADA, P.MAINTENANCE, P.SYNC, P.INTERACTIVE, P.AFTER_USER, P.INTERACTIVE) == \
        [P.INTERACTIVE, P.INTERACTIVE, P.AFTER_USER, P.SYNC, P.MAINTENANCE]


def test_a_job_for_every_account_waits_for_all_and_holds_them_all(per_account):
    t, release = holding(ADA)
    out = []
    every = queued(lambda: out.append(gates.run(gates.EVERY_ACCOUNT, lambda: (gates.held(ADA), gates.held(BEA)),
                                                priority=NOW)))
    assert out == []                                      # ADA's job still runs
    with pytest.raises(gates.Busy):                       # and a later BEA job waits behind it
        gates.run(BEA, lambda: "never", priority=gates.Priority.MAINTENANCE, wait=False)
    release.set()
    for w in (t, every):
        w.join(30)
    assert out == [(True, True)]
    assert not gates.held()


def test_a_job_for_every_account_lets_a_more_urgent_one_go_first(per_account):
    t, release = holding(ADA)
    ran = []
    every = queued(lambda: gates.run(gates.EVERY_ACCOUNT, lambda: ran.append("every"), priority=gates.Priority.MAINTENANCE))
    assert gates.run(BEA, lambda: "first", priority=NOW, wait=False) == "first"
    release.set()
    for w in (t, every):
        w.join(30)
    assert ran == ["every"]


def test_a_job_not_let_in_by_its_deadline_is_busy_and_never_runs(kind):
    t, release = holding(ADA)
    ran = []
    try:
        began = time.monotonic()
        with pytest.raises(gates.Busy):                   # by its deadline, while the gate is still held
            gates.run(ADA, lambda: ran.append(1), priority=NOW, deadline=0.2)
        assert time.monotonic() - began >= 0.15           # not at once
    finally:
        release.set()
        t.join(30)
    assert ran == []
    assert gates.run(ADA, lambda: "now", priority=NOW, deadline=0.2) == "now"


def test_a_job_past_its_deadline_lets_the_next_one_in(per_account):
    t, release = holding(ADA)
    out = []
    late = threading.Thread(target=lambda: out.append(gates.run(ADA, lambda: "late", priority=gates.Priority.SYNC)),
                            daemon=True)
    with pytest.raises(gates.Busy):
        gates.run(ADA, lambda: "never", priority=NOW, deadline=0.1)
    late.start()
    release.set()
    for w in (t, late):
        w.join(30)
    assert out == ["late"]


def test_a_job_on_one_account_may_take_another_without_waiting(per_account):
    def both():
        return gates.run(BEA, lambda: (gates.held(ADA), gates.held(BEA)), priority=NOW, wait=False)
    assert gates.run(ADA, both, priority=NOW) == (True, True)
    assert not gates.held()
