"""Connections kept open: a pooled one checked and replaced, and IDLE's worker, on what it does before a sync, how it stops
and an account removed."""
import threading
import time

import pytest

from fixtures import world as w
from waiting import wait_until
from world_helpers import acct, count, late


@pytest.fixture
def idle(monkeypatch):
    """realtime with its own stop flag, kick and dirty set, so stopping a worker here leaves the
    module as the next test expects it."""
    from mailmind.bootstrap.mailbox import realtime
    monkeypatch.setattr(realtime, "_stop", threading.Event())
    monkeypatch.setattr(realtime, "_kick", threading.Event())
    monkeypatch.setattr(realtime, "_dirty", set())
    monkeypatch.setattr(realtime, "_conns", [])
    monkeypatch.setattr(realtime, "_watchers", {})
    monkeypatch.setattr(realtime, "_running", False)
    workers = []

    def start(email=w.ADA):
        t = threading.Thread(target=realtime._worker, args=(acct(email),), name="idle-worker", daemon=True)
        t.start()
        workers.append(t)
        return t
    yield start
    realtime.stop()
    for t in workers:
        t.join(5)
    assert not any(t.is_alive() for t in workers), "an IDLE worker didn't stop"


def test_hwf_a_pooled_connection_is_checked_with_noop_and_dropped_after_an_error(world, monkeypatch):
    from mailmind.bootstrap.mailbox import pool
    monkeypatch.setenv("MAILMIND_POOL", w.ADA)
    monkeypatch.setattr(pool, "_slots", {})
    a, logins = acct(), count(world, "LOGIN")
    try:
        with pool.borrow(a) as first:
            first.select("INBOX")
        with pool.borrow(a) as again:
            assert again is first
        assert (count(world, "LOGIN"), count(world, "NOOP")) == (logins + 1, 1)    # reused after a NOOP
        with pytest.raises(RuntimeError):
            with pool.borrow(a):
                raise RuntimeError("a read went wrong")
        with pool.borrow(a) as fresh:
            assert fresh is not first                            # the slot was poisoned: a new login
        assert count(world, "LOGIN") == logins + 2
        world.server.drop("NOOP", user=w.ADA)
        with pool.borrow(a) as after_drop:
            assert after_drop is not fresh                       # failed its NOOP: replaced
        assert count(world, "LOGIN") == logins + 3
    finally:
        pool.close_all()


def test_hwf_the_idle_worker_leaves_idle_before_it_asks_for_a_sync(world, idle):
    from mailmind.bootstrap.mailbox import realtime
    idle()
    assert wait_until(lambda: count(world, "IDLE") == 1)
    n = len(world.server.commands)
    world.server.deliver(w.ADA, "INBOX", late())
    assert wait_until(lambda: count(world, "IDLE") == 2)          # DONE sent, then idling again
    assert realtime._dirty == {w.ADA} and realtime._kick.is_set()
    assert [c["command"] for c in world.server.commands[n:] if c["user"] == w.ADA] == ["IDLE"]   # no sync on it


def test_hwf_stopping_idle_leaves_closing_each_socket_to_its_worker(world, idle, monkeypatch):
    # stop() wakes a worker blocked in IDLE by shutting its socket down. Closing it from the stopping
    # thread instead freed the descriptor while the worker could still be about to wait on it, so
    # the number could pass to the next socket opened and the worker would wait on (and read) that.
    from mailmind.bootstrap.mailbox import realtime
    from mailmind.bootstrap import mailbox
    closed_by = []
    real = mailbox.open_session

    def open_session(acct, timeout=60):
        s = real(acct, timeout=timeout)
        sock = s._c.socket() if hasattr(s, "_c") else s._M.sock     # either adapter's TLS socket
        close = sock.close

        def spy():
            closed_by.append(threading.current_thread().name)
            close()
        sock.close = spy
        return s
    monkeypatch.setattr(mailbox, "open_session", open_session)
    worker = idle()
    assert wait_until(lambda: count(world, "IDLE") == 1)
    realtime.stop()
    worker.join(5)
    assert not worker.is_alive()
    assert closed_by and set(closed_by) == {"idle-worker"}


def test_hwf_an_idle_worker_stops_watching_a_removed_account(world, idle, monkeypatch):
    from mailmind.bootstrap.accounts import config
    from mailmind.bootstrap.mailbox import realtime
    monkeypatch.setattr(realtime, "IDLE_SECONDS", 0.1)
    idle()
    assert wait_until(lambda: count(world, "IDLE") >= 1)
    assert config.remove_account(w.ADA)
    time.sleep(0.3)                                              # a few IDLE cycles to notice
    n = len(world.server.commands_for(w.ADA))
    time.sleep(0.25)
    assert len(world.server.commands_for(w.ADA)) == n
