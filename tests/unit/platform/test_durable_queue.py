"""mailmind.platform.workers.durable_queue: the queue the outbox and the offline move queue share,
on a table of its own in a temp database."""
import threading
import time

import pytest

from mailmind.platform.db import database
from mailmind.platform.workers.durable_queue import DurableQueue
from waiting import wait_until

NOW = 1_800_000_000


@pytest.fixture
def q(tmp_path):
    path = str(tmp_path / "queue.db")
    c = database.connect(path)
    c.executescript("CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, state TEXT NOT NULL DEFAULT 'queued', "
                    "attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL DEFAULT 0, "
                    "done INTEGER NOT NULL DEFAULT 0, last_error TEXT, finished_at TEXT, what TEXT, lane TEXT)")
    c.close()
    return DurableQueue("jobs", "jobs", running="busy", finished="over", finished_at="finished_at", gate="done",
                        connect=lambda: database.connect(path), poll=0.05)


@pytest.fixture
def laned(q):
    """The same table, its rows in lanes; the worker drains only when kicked."""
    return DurableQueue("jobs", "jobs", running="busy", finished="over", finished_at="finished_at", gate="done",
                        connect=q._uow.connect, poll=30, lane="lane")


def run(q, fn):
    c = q._uow.connect()
    try:
        out = fn(c)
        c.commit()
        return out
    finally:
        c.close()


def add(q, what, **cols):
    cols = {"what": what, **cols}
    return run(q, lambda c: c.execute(f"INSERT INTO jobs({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                                      list(cols.values())).lastrowid)


def row(q, id_):
    return run(q, lambda c: dict(c.execute("SELECT * FROM jobs WHERE id=?", (id_,)).fetchone() or {}))


def test_the_rows_due_are_the_queued_ones_whose_time_has_come_oldest_first(q):
    later, first = add(q, "later", next_attempt_at=NOW + 1), add(q, "first", next_attempt_at=NOW - 5)
    add(q, "running", state="busy")
    add(q, "failed", state="error")
    second = add(q, "second", next_attempt_at=NOW)
    assert run(q, lambda c: q.due(c, NOW)) == [first, second]
    assert run(q, lambda c: q.due(c, NOW + 1)) == [later, first, second]


def test_only_one_claim_wins_a_row_and_gets_it_as_it_now_is(q):
    j = add(q, "send")
    got = run(q, lambda c: q.claim(c, j))
    assert (got["id"], got["state"], got["what"]) == (j, "busy", "send")
    assert run(q, lambda c: q.claim(c, j)) is None
    assert run(q, lambda c: q.claim(c, j + 1)) is None


def test_a_failed_attempt_backs_off_doubling_up_to_a_cap_and_the_fifth_is_the_last(q):
    j = add(q, "send")
    seen = []
    for _ in range(5):
        r = run(q, lambda c: q.claim(c, j))
        terminal, delay = run(q, lambda c: q.retry_later(c, r, "timeout", NOW))
        after = row(q, j)
        seen.append((terminal, delay, after["state"], after["attempts"], after["next_attempt_at"] - NOW))
    assert seen == [(False, 20, "queued", 1, 20), (False, 40, "queued", 2, 40), (False, 80, "queued", 3, 80),
                    (False, 160, "queued", 4, 160), (True, 320, "error", 5, 320)]
    assert row(q, j)["last_error"] == "timeout"
    assert [q.backoff(n) for n in (6, 7, 30)] == [640, 900, 900]


def test_a_failure_no_retry_can_fix_goes_to_error_at_once(q):
    j = add(q, "send", attempts=1)
    run(q, lambda c: q.give_up(c, q.claim(c, j), "account removed"))
    assert {k: row(q, j)[k] for k in ("state", "attempts", "next_attempt_at", "last_error")} == {
        "state": "error", "attempts": 2, "next_attempt_at": 0, "last_error": "account removed"}


def test_a_finished_row_records_when_and_forgets_its_last_error(q):
    j = add(q, "send", last_error="timeout")
    run(q, lambda c: q.finish(c, j, "2026-11-02T12:00:00+00:00"))
    assert {k: row(q, j)[k] for k in ("state", "finished_at", "last_error")} == {
        "state": "over", "finished_at": "2026-11-02T12:00:00+00:00", "last_error": None}


def test_a_crash_puts_a_running_row_back_with_its_gate_kept(q):
    sent, unsent = add(q, "sent"), add(q, "unsent")
    run(q, lambda c: (q.claim(c, sent), q.mark_gate(c, sent), q.claim(c, unsent)))
    run(q, q.reset_stuck)
    assert [(row(q, j)["state"], row(q, j)["done"]) for j in (sent, unsent)] == [("queued", 1), ("queued", 0)]


def test_only_a_row_in_error_can_be_retried_and_it_goes_at_once(q):
    failed, queued = add(q, "failed", state="error", attempts=5, next_attempt_at=NOW + 900, last_error="x"), add(q, "queued")
    assert run(q, lambda c: q.requeue(c, failed)) is True
    assert {k: row(q, failed)[k] for k in ("state", "attempts", "next_attempt_at", "last_error")} == {
        "state": "queued", "attempts": 0, "next_attempt_at": 0, "last_error": None}
    assert run(q, lambda c: q.requeue(c, queued)) is False


def test_the_wait_until_the_next_row_comes_due_is_at_most_the_poll(laned):
    assert run(laned, lambda c: laned.until_due(c, NOW)) == 30                  # nothing queued
    add(laned, "due already", next_attempt_at=NOW - 5)                          # a kick or the poll takes it
    add(laned, "failed", state="error", next_attempt_at=NOW + 1)
    assert run(laned, lambda c: laned.until_due(c, NOW)) == 30
    add(laned, "later", next_attempt_at=NOW + 600)
    assert run(laned, lambda c: laned.until_due(c, NOW)) == 30
    add(laned, "soon", next_attempt_at=NOW + 8)
    assert run(laned, lambda c: laned.until_due(c, NOW)) == 8
    assert run(laned, lambda c: laned.until_due(c, NOW + 9, NOW)) == 0          # due since the drain began to look
    assert run(laned, lambda c: laned.until_due(c, NOW + 9, NOW + 8)) == 30     # it looked at it then


def test_cancelling_before_the_claim_or_before_the_irreversible_step(q):
    """The outbox cancels only what no worker has claimed, since its send may be under way. The
    move queue cancels anything not yet applied: its worker re-reads the row before it moves."""
    queued, claimed, applied = add(q, "queued"), add(q, "claimed"), add(q, "applied")
    run(q, lambda c: (q.claim(c, claimed), q.claim(c, applied), q.mark_gate(c, applied)))
    assert [run(q, lambda c: q.cancel_queued(c, j)) for j in (claimed, applied)] == [False, False]
    assert run(q, lambda c: q.cancel_queued(c, queued)) is True and row(q, queued) == {}
    assert run(q, lambda c: q.cancel_unapplied(c, applied)) is False
    assert run(q, lambda c: q.cancel_unapplied(c, claimed)) is True and row(q, claimed) == {}


def test_a_row_back_in_the_queue_after_its_irreversible_step_cant_be_cancelled(q):
    """The outbox puts a delivered send back in the queue when its Sent copy fails, and a crash
    puts back whatever was running: Undo Send must not delete what already went out."""
    j = add(q, "delivered")
    run(q, lambda c: (q.claim(c, j), q.mark_gate(c, j)))
    run(q, q.reset_stuck)
    assert run(q, lambda c: q.cancel_queued(c, j)) is False
    assert (row(q, j)["state"], row(q, j)["done"]) == ("queued", 1)


def test_draining_runs_each_due_row_and_one_that_raises_stops_none_of_the_rest(q, capsys):
    ids = [add(q, w) for w in ("a", "b", "c")]
    add(q, "not yet", next_attempt_at=NOW + 60)
    handled = []

    def handle(id_):
        handled.append(id_)
        if id_ == ids[1]:
            raise ValueError("bad row")
    q.drain(handle, NOW)
    assert handled == ids
    out = capsys.readouterr().out
    assert f"[jobs] handle #{ids[1]} errored: ValueError" in out
    assert "bad row" not in out                          # a row's error can carry what its server said


def test_a_lane_waiting_on_its_server_holds_up_no_other_lane(laned):
    q = laned
    a1, b1, a2, b2 = (add(q, n, lane=n[0]) for n in ("a1", "b1", "a2", "b2"))
    release, handled = threading.Event(), []

    def handle(id_):
        handled.append((id_, threading.current_thread().name))
        if id_ == a1:
            release.wait(5)
    drain = threading.Thread(target=lambda: q.drain(handle, NOW), daemon=True)
    drain.start()
    try:
        assert wait_until(lambda: [i for i, _ in handled if i in (b1, b2)] == [b1, b2])
        assert a2 not in [i for i, _ in handled] and drain.is_alive()   # behind a1, and drain waits for both
    finally:
        release.set()
        drain.join(5)
    assert not drain.is_alive()
    assert [i for i, _ in handled if i in (a1, a2)] == [a1, a2]
    assert {(i in (a1, a2), name) for i, name in handled} == {(True, "jobs:a"), (False, "jobs:b")}


def test_the_workers_drain_passes_a_busy_lane_by_and_the_lane_kicks_it_as_it_ends(laned):
    q = laned
    release, handled, stop = threading.Event(), [], threading.Event()

    def handle(id_):
        if run(q, lambda c: q.claim(c, id_)) is None:
            return
        handled.append(id_)
        if id_ == a1:
            release.wait(5)
        run(q, lambda c: q.finish(c, id_, "2026-11-02T12:00:00+00:00"))
    a1 = add(q, "a1", lane="a")
    worker = threading.Thread(target=lambda: q.run_forever(lambda: q.drain(handle, NOW), stop), daemon=True)
    worker.start()
    try:
        q.kick()
        assert wait_until(lambda: handled == [a1])                # a's lane waits on its server
        a2, b1 = add(q, "a2", lane="a"), add(q, "b1", lane="b")
        q.kick()
        assert wait_until(lambda: b1 in handled), "the worker's drain waits on a's lane"
        assert a2 not in handled
        release.set()
        assert wait_until(lambda: handled == [a1, b1, a2]), "a's next row waits for the 30 s poll"
    finally:
        release.set()
        stop.set()
        q.kick()
        worker.join(5)
    assert not worker.is_alive()


def test_the_worker_wakes_when_a_row_comes_due_not_at_its_next_poll(laned):
    handled, stop = [], threading.Event()
    soon = add(laned, "soon", next_attempt_at=int(time.time()) + 1)
    worker = threading.Thread(target=lambda: laned.run_forever(
        lambda: laned.drain(handled.append, int(time.time())), stop), daemon=True)
    worker.start()                                               # its poll is 30 s
    try:
        assert wait_until(lambda: handled == [soon], timeout=3)
    finally:
        stop.set()
        laned.kick()
        worker.join(5)
    assert not worker.is_alive()


def test_a_row_that_comes_due_as_a_drain_ends_is_not_left_for_the_next_poll(laned):
    # The drain looks just before the row is due and is still going when it comes due. It didn't take
    # the row, and nothing will kick the queue for it.
    due = int(time.time()) + 2
    add(laned, "soon", next_attempt_at=due)
    stop, drains = threading.Event(), []

    def drain():
        now = int(time.time())
        drains.append(now)
        laned.drain(lambda id_: None, now)
        while time.time() < due + 0.2 and len(drains) == 1:      # still busy as it comes due
            time.sleep(0.01)

    worker = threading.Thread(target=lambda: laned.run_forever(drain, stop), daemon=True)
    worker.start()
    laned.kick()
    try:
        assert wait_until(lambda: len(drains) >= 2, timeout=10), "the row waited for the 30 s poll"
    finally:
        stop.set()
        laned.kick()
        worker.join(5)


def test_a_row_a_drain_wont_take_doesnt_spin_the_worker(laned):
    # Read-only's drain takes nothing, so a due row stays queued: the worker must not go round for it.
    add(laned, "due", next_attempt_at=int(time.time()) - 5)
    stop, drains = threading.Event(), []
    worker = threading.Thread(target=lambda: laned.run_forever(lambda: drains.append(1), stop), daemon=True)
    worker.start()
    laned.kick()
    try:
        assert wait_until(lambda: drains)
        time.sleep(0.3)
        assert len(drains) == 1
    finally:
        stop.set()
        laned.kick()
        worker.join(5)


def test_a_row_coming_due_just_after_a_drain_that_wont_take_it_gets_one_more_look_and_no_more(laned):
    due = int(time.time()) + 1
    add(laned, "soon", next_attempt_at=due)
    stop, drains = threading.Event(), []
    worker = threading.Thread(target=lambda: laned.run_forever(lambda: drains.append(time.time()), stop), daemon=True)
    worker.start()
    laned.kick()
    try:
        assert wait_until(lambda: drains and drains[-1] >= due, timeout=5)
        time.sleep(0.3)
        assert sum(1 for t in drains if t >= due) == 1
    finally:
        stop.set()
        laned.kick()
        worker.join(5)


def test_the_worker_puts_back_what_a_crash_left_then_drains_when_kicked(q):
    stuck = add(q, "stuck")
    run(q, lambda c: q.claim(c, stuck))
    handled, woke, stop = [], threading.Event(), threading.Event()

    def handle(id_):
        handled.append(id_)
        woke.set()
    worker = threading.Thread(target=lambda: q.run_forever(lambda: q.drain(handle, NOW), stop), daemon=True)
    worker.start()
    try:
        assert woke.wait(5) and handled == [stuck]
        woke.clear()
        fresh = add(q, "fresh")
        q.kick()
        assert woke.wait(5) and handled[-1] == fresh
    finally:
        stop.set()
        q.kick()
        worker.join(5)
    assert not worker.is_alive()
