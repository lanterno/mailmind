"""Muting a thread marks it read on the server too (decision D6), through the same queue as any flag
change, and Undo marks unread again only what muting marked read."""
from datetime import timedelta

from fixtures import world as w
from mailmind.bootstrap import attention

LUNCH = "ada-inbox-02-lunch-confirm.eml"


def acct():
    from mailmind.bootstrap import accounts
    return accounts.get(w.ADA)


def seen_on_server(world, uid, user=w.ADA, folder="INBOX"):
    return w.SEEN in world.server.messages(user, folder)[uid].flags


def thread_with_a_reply(world):
    """The lunch thread with a second, unread message, as `(row, uid of the reply)`."""
    lunch = world.row(LUNCH)
    reply = w.variant(LUNCH, Message_ID="<mute-reply@example.test>", In_Reply_To=lunch["message_id"])
    return lunch, world.deliver(w.ADA, "INBOX", reply)


def row_of(uid):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return c.execute("SELECT * FROM messages WHERE account=? AND folder='INBOX' AND uid=?", (w.ADA, uid)).fetchone()
    finally:
        c.close()


def test_muting_marks_the_unread_members_read_on_the_server(app, world):
    from mailmind.bootstrap.mailbox import offline
    lunch, uid = thread_with_a_reply(world)
    assert not seen_on_server(world, uid) and row_of(uid)["seen"] == 0
    assert app.post("/api/mute", {"pk": lunch["pk"]}).json["ok"]
    assert row_of(uid)["seen"] == 1                              # here at once, in the request
    assert not seen_on_server(world, uid)
    offline.drain()
    assert seen_on_server(world, uid)


def test_muting_a_thread_already_read_asks_nothing_of_the_server(app, world):
    from mailmind.bootstrap.mailbox import store
    lunch, uid = thread_with_a_reply(world)
    c = store.conn()
    c.execute("UPDATE messages SET seen=1 WHERE account=? AND thread_id=?", (w.ADA, lunch["thread_id"]))
    c.commit()
    c.close()
    assert app.post("/api/mute", {"pk": lunch["pk"]}).json["ok"]
    assert app.get("/api/pending").json["items"] == []


def test_muting_again_queues_nothing_more(app, world):
    lunch, uid = thread_with_a_reply(world)
    assert app.post("/api/mute", {"pk": lunch["pk"]}).json["ok"]
    before = len(app.get("/api/pending").json["items"])
    assert before and app.post("/api/mute", {"pk": lunch["pk"]}).json["ok"]
    assert len(app.get("/api/pending").json["items"]) == before


def test_undo_marks_unread_what_muting_marked_read_and_no_more(app, world):
    from mailmind.bootstrap.mailbox import offline
    lunch, uid = thread_with_a_reply(world)
    other = world.deliver(w.ADA, "INBOX", w.variant(LUNCH, Message_ID="<mute-read@example.test>",
                                                    In_Reply_To=lunch["message_id"]), flags=[w.SEEN])
    assert app.post("/api/mute", {"pk": lunch["pk"]}).json["ok"]
    offline.drain()
    assert seen_on_server(world, uid)
    assert app.post("/api/undo", {}).json["ok"]
    assert row_of(uid)["seen"] == 0 and row_of(other)["seen"] == 1
    offline.drain()
    assert not seen_on_server(world, uid)
    assert seen_on_server(world, other)                          # it was read before the mute


def test_undo_before_the_mark_landed_ends_up_unread(app, world):
    from mailmind.bootstrap.mailbox import offline
    lunch, uid = thread_with_a_reply(world)
    assert app.post("/api/mute", {"pk": lunch["pk"]}).json["ok"]
    assert app.post("/api/undo", {}).json["ok"]
    offline.drain()
    assert not seen_on_server(world, uid) and row_of(uid)["seen"] == 0


def test_undo_finds_the_row_after_a_renumber(app, world):
    import ingest
    from mailmind.bootstrap.mailbox import offline
    lunch, uid = thread_with_a_reply(world)
    assert app.post("/api/mute", {"pk": lunch["pk"]}).json["ok"]
    offline.drain()
    world.server.renumber(w.ADA, "INBOX")
    ingest.sync_new(acct())
    assert app.post("/api/undo", {}).json["ok"]
    offline.drain()
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    r = c.execute("SELECT seen FROM messages WHERE account=? AND folder='INBOX' AND message_id=?",
                  (w.ADA, "<mute-reply@example.test>")).fetchone()
    c.close()
    assert r["seen"] == 0


def test_an_undo_written_before_this_only_changes_the_local_row(app, world):
    from mailmind.bootstrap.mailbox import store
    lunch, uid = thread_with_a_reply(world)
    pk = row_of(uid)["pk"]
    c = store.conn()
    attention.book.mute_thread(c, w.ADA, lunch["thread_id"], "2026-11-01T00:00:00+00:00")
    store.set_local_flags(c, pk, seen=1)
    store.undo_put_back(c, {"kind": "mute", "account": w.ADA, "thread_id": lunch["thread_id"],
                            "prev_seen": [[pk, 0]], "label": "Muted"})
    c.commit()
    c.close()
    assert app.post("/api/undo", {}).json["ok"]
    assert row_of(uid)["seen"] == 0
    assert app.get("/api/pending").json["items"] == []


def test_a_refused_mark_goes_to_the_tray_and_isnt_asked_again_each_sync(app, world):
    import ingest
    from mailmind.bootstrap.mailbox import offline
    lunch, uid = thread_with_a_reply(world)
    assert app.post("/api/mute", {"pk": lunch["pk"]}).json["ok"]
    world.server.fail("UID STORE", user=w.ADA, times=None)
    offline.drain()
    assert row_of(uid)["seen"] == 0                              # as the server has it
    held = app.get("/api/pending").json
    assert held["stuck"] >= 1
    app.clock.shift(timedelta(minutes=5))                        # a later sync, or the mark's id alone would dedupe
    ingest.sync_new(acct())
    assert app.get("/api/pending").json["stuck"] == held["stuck"]
    assert row_of(uid)["seen"] == 0


def test_a_reply_after_a_marked_one_is_marked_as_well(app, world):
    import ingest
    from mailmind.bootstrap.mailbox import offline
    lunch, uid = thread_with_a_reply(world)
    assert app.post("/api/mute", {"pk": lunch["pk"]}).json["ok"]
    offline.drain()
    world.server.deliver(w.ADA, "INBOX", w.variant(LUNCH, Message_ID="<mute-later@example.test>",
                                                   In_Reply_To=lunch["message_id"]))
    ingest.sync_new(acct())
    offline.drain()
    assert all(seen_on_server(world, u) for u, m in world.server.messages(w.ADA, "INBOX").items()
               if b"mute-" in m.raw)


def test_read_only_mutes_and_the_mark_waits_in_the_queue(app, world, monkeypatch):
    from mailmind.bootstrap.mailbox import offline
    lunch, uid = thread_with_a_reply(world)
    monkeypatch.setenv("MAILMIND_PROFILE", "read-only")
    since = world.mark()
    assert app.post("/api/mute", {"pk": lunch["pk"]}).json["ok"]
    offline.drain()
    assert world.writes(since) == []
    assert row_of(uid)["seen"] == 1
    assert app.get("/api/pending").json["waiting"] >= 1


def test_finished_queue_rows_go_after_a_month(app, world):
    from mailmind.bootstrap.mailbox import offline
    import server
    from mailmind.bootstrap.mailbox import store
    lunch, uid = thread_with_a_reply(world)
    assert app.post("/api/mute", {"pk": lunch["pk"]}).json["ok"]
    offline.drain()

    def count():
        c = store.conn()
        try:
            return c.execute("SELECT COUNT(*) FROM read_mutations WHERE state='done'").fetchone()[0]
        finally:
            c.close()
    done = count()
    assert done
    server.forget_round()
    assert count() == done
    app.clock.shift(timedelta(days=31))
    server.forget_round()
    assert count() == 0


def test_an_unfinished_queue_row_stays_past_a_month(app, world):
    import server
    lunch, uid = thread_with_a_reply(world)
    assert app.post("/api/mute", {"pk": lunch["pk"]}).json["ok"]
    open_rows = len(app.get("/api/pending").json["items"])
    app.clock.shift(timedelta(days=31))
    server.forget_round()
    assert len(app.get("/api/pending").json["items"]) == open_rows
