"""The offline mutation queue (readmutq): an archive, trash, junk or move asked for while IMAP is
unreachable is queued, its message left out of the lists at once (the row stays, marked
pending_removal), and the worker applies it when the server is back. That happens exactly once,
even across a crash, with backoff while the server stays away. A move the server refuses, or one
the queue gives up on, lists its message again, and Undo cancels a queued move that hasn't
shipped without asking the server. The worker doesn't run here: each test calls
readmutq.drain() itself and moves the frozen clock past the backoff.
"""
from datetime import timedelta

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
    assert world.server.commands[since:] == []                         # nothing asked of the server
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


def test_a_queued_move_leaves_its_message_out_of_the_lists_until_it_lands(app, world):
    import store
    pk, thread = world.pk(RECEIPT), world.row(RECEIPT)["thread_id"]
    queue(app, world)
    listed = app.get(f"/api/messages?account={w.ADA}&folder=INBOX").json["items"]
    assert pk not in [m["pk"] for m in listed]
    assert app.get("/api/search?scope=all&q=receipt+1042").json["items"] == []
    assert app.get(f"/api/thread?account={w.ADA}&thread_id={thread}").json["items"] == []
    [r] = rows()
    c = store.conn()
    assert store.pending_removal_pks(c, r["id"]) == [pk]                # kept, for a move that doesn't happen
    c.close()
    drain()
    c = store.conn()
    assert store.pending_removal_pks(c, r["id"]) == [] and store.get_message(c, pk) is None
    c.close()


def test_a_landed_move_drops_its_own_row_not_the_copy_a_sync_stored(app, world, monkeypatch):
    import accounts
    import ingest
    import readmutq
    ingest.sync_folder(accounts.get(w.ADA), "Archive", full=True)
    queue(app, world)
    mirror = readmutq._local_mirror

    def archive_synced_first(row):
        ingest.sync_new(accounts.get(w.ADA), "Archive")                # the moved copy is stored before
        return mirror(row)
    monkeypatch.setattr(readmutq, "_local_mirror", archive_synced_first)
    drain()
    assert boxes(world, RECEIPT) == ([(w.ADA, "Archive")], [(w.ADA, "Archive")])


def flag_queued(app, world, path="/api/seen", body=None, name=RECEIPT, mutation_id="f-1"):
    """Ask for a flag change on `name` while the server drops the connection."""
    world.server.drop("SELECT", user=w.ADA)
    r = app.post(path, {"pk": world.pk(name), "mutation_id": mutation_id, **(body or {"seen": True})}).json
    assert r["status"] == "queued", r
    return r


def flags_here(world, name=RECEIPT):
    """(seen, flagged) on the message's local row."""
    r = world.row(name)
    return r["seen"], r["flagged"]


def test_a_queued_flag_change_shows_at_once_and_applies_once_the_server_is_back(app, world):
    flag_queued(app, world, "/api/flag", {"flag": "flagged", "on": True})
    assert flags_here(world) == (0, 1)
    since = world.mark()
    drain()
    assert world.writes(since) == [f"{w.ADA} [INBOX] UID STORE {world.uid(RECEIPT)} +FLAGS (\\Flagged)"]
    assert (rows()[0]["state"], rows()[0]["applied"]) == ("done", 1)
    assert flags_here(world) == (0, 1)


def test_a_queued_flag_change_the_server_refuses_is_put_back(app, world):
    flag_queued(app, world)
    assert flags_here(world) == (1, 0)
    world.server.fail("UID STORE", user=w.ADA)
    drain()
    assert rows()[0]["state"] == "error"
    assert flags_here(world) == (0, 0)                                      # as the server still has it
    assert w.SEEN not in world.server.messages(w.ADA, "INBOX")[world.uid(RECEIPT)].flags


def test_a_queued_flag_change_given_up_on_is_put_back(app, world):
    import readmutq
    flag_queued(app, world)
    world.server.drop("SELECT", user=w.ADA, times=readmutq.MAX_ATTEMPTS)
    for _ in range(readmutq.MAX_ATTEMPTS):
        drain()
        app.clock.shift(timedelta(seconds=readmutq._BACKOFF_CAP + 1))
    assert rows()[0]["state"] == "error"
    assert flags_here(world) == (0, 0)


def test_a_copy_flagged_deleted_in_the_destination_isnt_the_copy_a_retry_looks_for(app, world):
    """A refused EXPUNGE left it there. Taken for an earlier try's COPY, the live original would be
    removed, and the next expunge from any client would leave nothing."""
    import readmutq
    world.server.without.add("MOVE")
    world.server.deliver(w.ADA, "Archive", w.raw(RECEIPT), flags=("\\Deleted",))
    queue(app, world)
    readmutq.drain()
    live = [box for _, box, _, flags in world.where(RECEIPT)["server"] if "\\Deleted" not in flags]
    assert live == ["Archive"]


def test_a_move_that_landed_says_so_before_the_imap_gate_lets_anyone_in(app, world, monkeypatch):
    """A Discard or an Undo waiting for the gate must find the move marked, or it would cancel a
    move that has already happened and list a message the server has moved."""
    import readmutq
    from mailmind.platform.workers import gates
    queue(app, world)
    real, at_release = gates.run, []

    def noting(account, fn, **kw):
        out = real(account, fn, **kw)
        at_release.append(rows()[0]["applied"])
        return out
    monkeypatch.setattr(gates, "run", noting)
    readmutq.drain()
    assert at_release == [1]


def test_a_queued_move_the_server_refuses_comes_back(app, world):
    queue(app, world)
    world.server.fail("UID MOVE", user=w.ADA)
    world.server.fail("UID COPY", user=w.ADA)
    drain()
    assert boxes(world, RECEIPT) == ([(w.ADA, "INBOX")], [(w.ADA, "INBOX")])
    assert rows()[0]["state"] == "error"


def test_a_queued_move_given_up_on_comes_back(app, world):
    import readmutq
    queue(app, world)
    world.server.drop("SELECT", user=w.ADA, times=readmutq.MAX_ATTEMPTS)
    for _ in range(readmutq.MAX_ATTEMPTS):
        drain()
        app.clock.shift(timedelta(seconds=readmutq._BACKOFF_CAP + 1))
    assert rows()[0]["state"] == "error"
    assert boxes(world, RECEIPT) == ([(w.ADA, "INBOX")], [(w.ADA, "INBOX")])
