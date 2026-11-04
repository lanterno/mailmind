"""The offline mutation queue (readmutq): an archive, trash, junk or move asked for while IMAP is
unreachable is queued, its local row dropped at once, and the worker applies it when the server
is back. That happens exactly once, even across a crash, with backoff while the server stays
away, and Undo cancels a queued move that hasn't shipped. The worker doesn't run here: each test
calls readmutq.drain() itself and moves the frozen clock past the backoff.
"""
from datetime import timedelta

import pytest

from fixtures import world as w

RECEIPT = "ada-inbox-05-receipt.eml"


def queue(app, world, op="archive", name=RECEIPT, account=w.ADA, mutation_id="m-1", **body):
    """Ask for `op` on `name` while the server drops the connection."""
    world.server.drop("SELECT", user=account)
    r = app.post(f"/api/{op}", {"pk": world.pk(name), "mutation_id": mutation_id, **body}).json
    assert r["status"] == "queued", r
    return r


def rows():
    import store
    c = store.conn()
    try:
        return [{k: r[k] for k in ("id", "op", "state", "attempts", "applied", "dest", "last_error",
                                   "next_attempt_at")} for r in c.execute("SELECT * FROM read_mutations ORDER BY id")]
    finally:
        c.close()


def drain():
    import readmutq
    readmutq.drain()


def boxes(world, name):
    """Where `name` is: [(account, folder)] on the server, and in the database."""
    got = world.where(name)
    return sorted((s[0], s[1]) for s in got["server"]), sorted((d["account"], d["folder"]) for d in got["db"])


def test_a_queued_archive_applies_once_the_server_is_back(app, world):
    queue(app, world)
    assert boxes(world, RECEIPT) == ([(w.ADA, "INBOX")], [])           # gone locally, still on the server
    since = world.mark()
    drain()
    assert world.writes(since) == [f'{w.ADA} [INBOX] UID MOVE {world.uid(RECEIPT)} "Archive"']
    assert boxes(world, RECEIPT)[0] == [(w.ADA, "Archive")]
    [r] = rows()
    assert (r["state"], r["applied"], r["dest"], r["attempts"]) == ("done", 1, "Archive", 0)
    since = world.mark()
    drain()                                                            # nothing left to do
    assert world.writes(since) == []


def test_a_queued_gmail_archive_drops_the_inbox_label(app, world):
    name = "cy-inbox-03-unrelated.eml"
    uid = world.uid(name)
    queue(app, world, name=name, account=w.CY)
    since = world.mark()
    drain()
    assert world.writes(since) == [f"{w.CY} [INBOX] UID STORE {uid} -X-GM-LABELS (\\Inbox)"]
    assert (w.CY, "INBOX") not in boxes(world, name)[0] and (w.CY, "[Gmail]/All Mail") in boxes(world, name)[0]


def test_while_the_server_stays_away_it_backs_off_then_gives_up(app, world):
    import readmutq
    queue(app, world, op="trash")
    world.server.drop("SELECT", user=w.ADA, times=None)
    seen = []
    for _ in range(readmutq.MAX_ATTEMPTS):
        drain()
        [r] = rows()
        seen.append((r["state"], r["attempts"], r["last_error"]))
        drain()                                                        # not due yet: nothing happens
        assert rows()[0]["attempts"] == r["attempts"]
        app.clock.shift(timedelta(seconds=readmutq._BACKOFF_CAP + 1))
    assert seen == [("queued", n, "abort") for n in range(1, readmutq.MAX_ATTEMPTS)] + [
        ("error", readmutq.MAX_ATTEMPTS, "abort")]
    world.server.clear_faults()
    since = world.mark()
    drain()
    assert world.writes(since) == []                                   # given up stays given up
    assert boxes(world, RECEIPT)[0] == [(w.ADA, "INBOX")]


def test_a_crash_after_the_move_never_moves_it_twice(app, world, monkeypatch):
    import readmutq
    import store
    queue(app, world)

    mirror, crashed = readmutq._local_mirror, []

    def crash_once(row):
        if not crashed:
            crashed.append(row["id"])
            raise RuntimeError("the process died here")
        return mirror(row)

    monkeypatch.setattr(readmutq, "_local_mirror", crash_once)
    drain()                                                            # moved, then "crashed"
    assert (rows()[0]["state"], rows()[0]["applied"]) == ("running", 1)
    c = store.conn()
    readmutq.QUEUE.reset_stuck(c)                                      # what the worker does at boot
    c.commit()
    c.close()
    since = world.mark()
    drain()
    assert world.writes(since) == []                                   # the move is not sent again
    assert (rows()[0]["state"], rows()[0]["applied"]) == ("done", 1)
    assert boxes(world, RECEIPT)[0] == [(w.ADA, "Archive")]


def test_an_applied_move_is_published_once_even_across_a_crash(app, world, monkeypatch):
    import readmutq
    import store
    from mailmind.platform import bus
    heard = []

    def crash(row):
        raise RuntimeError("the process died here")
    stop = bus.subscribe(readmutq.Applied, heard.append)
    try:
        queue(app, world)
        mirror = readmutq._local_mirror
        monkeypatch.setattr(readmutq, "_local_mirror", crash)
        drain()                                                        # moved, then "crashed": not yet
        assert heard == []
        monkeypatch.setattr(readmutq, "_local_mirror", mirror)
        c = store.conn()
        readmutq.QUEUE.reset_stuck(c)
        c.commit()
        c.close()
        drain()
        drain()
    finally:
        stop()
    [r] = rows()
    assert heard == [readmutq.Applied(r["id"], w.ADA)]


def test_undo_cancels_a_queued_move_that_hasnt_shipped(app, world):
    queue(app, world)
    since = world.mark()
    assert app.post("/api/undo", {}).json["ok"]
    assert rows() == []                                                # cancelled
    drain()
    assert world.writes(since) == []
    assert boxes(world, RECEIPT) == ([(w.ADA, "INBOX")], [(w.ADA, "INBOX")])   # back in the list


def test_undo_after_the_queue_shipped_moves_it_back(app, world):
    queue(app, world)
    drain()
    since = world.mark()
    assert app.post("/api/undo", {}).json["ok"]
    assert [x.split(" UID MOVE ")[0] + " MOVE" + x.split(" UID MOVE ")[1].split(" ", 1)[1]
            for x in world.writes(since)] == [f'{w.ADA} [Archive] MOVE"INBOX"']
    assert boxes(world, RECEIPT) == ([(w.ADA, "INBOX")], [(w.ADA, "INBOX")])


# ── known bugs ───────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="known bug: a queued move the server refuses still drops the local row")
def test_a_queued_move_the_server_refuses_comes_back(app, world):
    queue(app, world)
    world.server.fail("UID MOVE", user=w.ADA)
    world.server.fail("UID COPY", user=w.ADA)
    drain()
    assert boxes(world, RECEIPT) == ([(w.ADA, "INBOX")], [(w.ADA, "INBOX")])
    assert rows()[0]["state"] == "error"


@pytest.mark.xfail(strict=True, reason="known bug: a queued move given up on leaves the message out of the list")
def test_a_queued_move_given_up_on_comes_back(app, world):
    import readmutq
    queue(app, world)
    world.server.drop("SELECT", user=w.ADA, times=readmutq.MAX_ATTEMPTS)
    for _ in range(readmutq.MAX_ATTEMPTS):
        drain()
        app.clock.shift(timedelta(seconds=readmutq._BACKOFF_CAP + 1))
    assert rows()[0]["state"] == "error"
    assert boxes(world, RECEIPT) == ([(w.ADA, "INBOX")], [(w.ADA, "INBOX")])
