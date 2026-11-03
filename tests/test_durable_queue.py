"""mailmind.platform.workers.durable_queue: the queue the outbox and the offline move queue share,
on a table of its own in a temp database."""
import threading

import pytest

from mailmind.platform.db import database
from mailmind.platform.workers.durable_queue import DurableQueue

NOW = 1_800_000_000


@pytest.fixture
def q(tmp_path):
    path = str(tmp_path / "queue.db")
    c = database.connect(path)
    c.executescript("CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, state TEXT NOT NULL DEFAULT 'queued', "
                    "attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL DEFAULT 0, "
                    "done INTEGER NOT NULL DEFAULT 0, last_error TEXT, finished_at TEXT, what TEXT)")
    c.close()
    return DurableQueue("jobs", "jobs", running="busy", finished="over", finished_at="finished_at", gate="done",
                        connect=lambda: database.connect(path), poll=0.05)


def run(q, fn):
    c = q._connect()
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
    failed, queued = add(q, "failed", state="error", next_attempt_at=NOW + 900, last_error="x"), add(q, "queued")
    assert run(q, lambda c: q.requeue(c, failed)) is True
    assert {k: row(q, failed)[k] for k in ("state", "next_attempt_at", "last_error")} == {
        "state": "queued", "next_attempt_at": 0, "last_error": None}
    assert run(q, lambda c: q.requeue(c, queued)) is False


def test_cancelling_before_the_claim_or_before_the_irreversible_step(q):
    """The outbox cancels only what no worker has claimed, since its send may be under way. The
    move queue cancels anything not yet applied: its worker re-reads the row before it moves."""
    queued, claimed, applied = add(q, "queued"), add(q, "claimed"), add(q, "applied")
    run(q, lambda c: (q.claim(c, claimed), q.claim(c, applied), q.mark_gate(c, applied)))
    assert [run(q, lambda c: q.cancel_queued(c, j)) for j in (claimed, applied)] == [False, False]
    assert run(q, lambda c: q.cancel_queued(c, queued)) is True and row(q, queued) == {}
    assert run(q, lambda c: q.cancel_unapplied(c, applied)) is False
    assert run(q, lambda c: q.cancel_unapplied(c, claimed)) is True and row(q, claimed) == {}


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
    assert f"[jobs] handle #{ids[1]} errored: ValueError: bad row" in capsys.readouterr().out


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
