"""What a sync stores: a row at a time with the watermark after them all, and flags changed elsewhere, with CONDSTORE and without."""
import pytest

from fixtures import world as w
from stored_rows import A, q
from world_helpers import acct, late, stored


def test_hwf_an_upsert_of_a_stored_uid_keeps_its_sender_date_and_ids(db):
    from mailmind.bootstrap.mailbox import store
    first = {"account": A, "kind": "gmail", "folder": "INBOX", "uid": 7, "message_id": "<one@example.test>",
             "gmail_thread": "1781", "from_addr": "lin@example.test", "from_name": "Lin Mori", "subject": "Old",
             "date_iso": "2026-10-01T09:00:00+00:00", "ts": 100, "snippet": "old", "bucket": "other/personal-ish",
             "confidence": "low", "reason": "r1", "is_junk": 0}
    pk = store.upsert_message(db, first)
    again = store.upsert_message(db, {**first, "message_id": "<two@example.test>", "gmail_thread": "1799",
                                      "from_addr": "x@example.test", "from_name": "X", "subject": "New",
                                      "date_iso": "2026-11-01T09:00:00+00:00", "ts": 200, "snippet": "new",
                                      "bucket": "blocked", "confidence": "high", "reason": "r2", "is_junk": 1})
    row = dict(store.get_message(db, pk))
    assert again == pk
    assert {k: row[k] for k in ("message_id", "gmail_thread", "from_addr", "from_name", "date_iso", "ts")} == {
        "message_id": "<one@example.test>", "gmail_thread": "1781", "from_addr": "lin@example.test",
        "from_name": "Lin Mori", "date_iso": "2026-10-01T09:00:00+00:00", "ts": 100}
    assert {k: row[k] for k in ("subject", "snippet", "bucket", "confidence", "reason", "is_junk")} == {
        "subject": "New", "snippet": "new", "bucket": "blocked", "confidence": "high", "reason": "r2", "is_junk": 1}


def watermark(folder="INBOX", email=w.ADA):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return store.get_watermark(c, email, folder)
    finally:
        c.close()


def test_hwf_a_bounded_first_sync_takes_the_newest_uids(world):
    from mailmind.bootstrap.mailbox import ingest
    for n in range(3):
        world.server.deliver(w.ADA, "Junk", late(n))
    ingest.sync_folder(acct(), "Junk", recent=2)          # how folder warming first touches a folder
    assert stored("Junk") == {2: "<late-1@example.test>", 3: "<late-2@example.test>"}


def test_hwf_rows_commit_one_by_one_and_the_watermark_moves_only_after_all(world, monkeypatch):
    from mailmind.bootstrap.mailbox import ingest
    from mailmind.bootstrap.mailbox import store
    wm = watermark()
    uids = [world.server.deliver(w.ADA, "INBOX", late(n)) for n in range(3)]
    real, seen = store.upsert_message, []

    def dies_on_the_third(c, m):
        seen.append(m["uid"])
        if len(seen) == 3:
            raise RuntimeError("the process died here")
        return real(c, m)
    monkeypatch.setattr(store, "upsert_message", dies_on_the_third)
    with pytest.raises(RuntimeError):
        ingest.sync_new(acct())
    assert [u for u in uids if u in stored()] == uids[:2]        # what was stored before the crash stays
    assert watermark() == wm                                     # but the folder isn't marked synced past it
    monkeypatch.setattr(store, "upsert_message", real)
    ingest.sync_new(acct())
    assert [u for u in uids if u in stored()] == uids and watermark() == uids[-1]


def test_hwf_mail_read_in_another_client_shows_read_after_a_sync(app, world):
    from mailmind.bootstrap.mailbox import ingest
    name = "ada-inbox-02-lunch-confirm.eml"
    with world.server.lock:
        world.server.mailbox(w.ADA, "INBOX").messages[world.uid(name)].flags.add(w.SEEN)
    ingest.sync_new(acct())
    assert world.row(name)["seen"] == 1


def test_hwf_a_sent_copy_flagged_deleted_doesnt_count_as_filed(world):
    from mailmind.bootstrap import mailbox
    raw = w.raw("ada-sent-01-lunch-reply.eml")
    mid = w.message_id("ada-sent-01-lunch-reply.eml")
    with world.server.lock:
        box = world.server.mailbox(w.ADA, "Sent")
        box.messages[world.uid("ada-sent-01-lunch-reply.eml")].flags.add("\\Deleted")
    assert mailbox.copies.append_to_sent(acct(), raw, mid) == "Sent"
    with world.server.lock:
        flags = [m.flags for m in box.messages.values()]
    assert sorted("\\Deleted" in f for f in flags) == [False, True]


def modseq(folder="INBOX", email=w.ADA):
    return q("SELECT highest_modseq FROM sync_state WHERE account=? AND folder=?", email, folder)[0]["highest_modseq"]


def flag_fetches(world, since, email=w.ADA):
    """The UID FETCHes of flags alone sent since `since`, as their arguments."""
    return [c["args"] for c in world.server.commands[since:]
            if c["user"] == email and c["command"] == "UID FETCH" and c["args"][1] == ["FLAGS"]]


def elsewhere(world, name, folder="INBOX", email=w.ADA):
    with world.server.lock:
        world.server.mailbox(email, folder).messages[world.uid(name)].flags.add("\\Flagged")


def test_hwf_a_sync_with_nothing_changed_reads_no_flags(world):
    from mailmind.bootstrap.mailbox import ingest
    assert modseq()                                   # the first sync, a whole scan, left the mark
    since = len(world.server.commands)
    ingest.sync_new(acct())
    assert flag_fetches(world, since) == []


def test_hwf_a_sync_asks_only_for_what_changed_since_its_mark(world):
    from mailmind.bootstrap.mailbox import ingest
    name, mark = "ada-inbox-02-lunch-confirm.eml", modseq()
    elsewhere(world, name)
    since = len(world.server.commands)
    ingest.sync_new(acct())
    [fetch] = flag_fetches(world, since)
    assert fetch == [str(world.uid(name)), ["FLAGS"]]
    assert world.row(name)["flagged"] == 1
    assert modseq() > mark


def test_hwf_a_change_to_mail_older_than_the_newest_is_seen_too(world, monkeypatch):
    from mailmind.bootstrap.mailbox import ingest
    monkeypatch.setattr(ingest, "FLAG_REFRESH", 1)
    oldest = min(((world.row(n)["uid"], n) for n, acct_, folder, *_ in w.MAIL if acct_ == w.ADA and folder == "INBOX"))[1]
    elsewhere(world, oldest)
    ingest.sync_new(acct())
    assert world.row(oldest)["flagged"] == 1


def test_hwf_a_server_with_no_condstore_has_its_newest_read_each_time(world):
    from mailmind.bootstrap.mailbox import ingest
    world.server.without.add("CONDSTORE")
    name = "ada-inbox-02-lunch-confirm.eml"
    elsewhere(world, name)
    since = len(world.server.commands)
    ingest.sync_new(acct())
    [fetch] = flag_fetches(world, since)
    assert str(world.uid(name)) in str(fetch[0]).split(",") and "," in str(fetch[0])    # all the newest
    assert world.row(name)["flagged"] == 1


def test_hwf_a_refused_modseq_search_falls_back_to_the_newest(world):
    from mailmind.bootstrap.mailbox import ingest
    name, mark = "ada-inbox-02-lunch-confirm.eml", modseq()
    elsewhere(world, name)
    world.server.fail("UID SEARCH", "BAD [scripted] no such key", user=w.ADA,
                      when=lambda a: str(a[0]).upper() == "MODSEQ")
    ingest.sync_new(acct())
    assert world.row(name)["flagged"] == 1
    assert modseq() == mark                           # what's older than the newest is still to ask


def test_hwf_a_refused_flag_read_leaves_the_mark_so_the_next_sync_asks_again(world):
    from mailmind.bootstrap.mailbox import ingest
    name, mark = "ada-inbox-02-lunch-confirm.eml", modseq()
    elsewhere(world, name)
    world.server.fail("UID SEARCH", user=w.ADA, when=lambda a: str(a[0]).upper() == "MODSEQ")
    world.server.fail("UID FETCH", user=w.ADA, when=lambda a: a[1] == ["FLAGS"])
    ingest.sync_new(acct())
    assert (world.row(name)["flagged"], modseq()) == (0, mark)
    ingest.sync_new(acct())
    assert world.row(name)["flagged"] == 1


def test_hwf_a_click_while_the_flags_are_read_keeps_its_own_when_the_server_said_what_changed(world, monkeypatch):
    from mailmind.bootstrap.mailbox import ingest
    from mailmind.bootstrap.mailbox import store
    from mailmind.mailbox.adapters.imap.imaplib_session import ImaplibSession
    name = "ada-inbox-02-lunch-confirm.eml"
    elsewhere(world, name)                            # flagged elsewhere; here it's marked read meanwhile
    real, clicked = ImaplibSession.flags, []

    def click_lands_first(self, uids):
        if not clicked:
            clicked.append(True)
            c = store.conn()
            c.execute("UPDATE messages SET seen=1 WHERE pk=?", (world.pk(name),))
            c.commit()
            c.close()
        return real(self, uids)
    monkeypatch.setattr(ImaplibSession, "flags", click_lands_first)
    ingest.sync_new(acct())
    assert clicked and (world.row(name)["seen"], world.row(name)["flagged"]) == (1, 0)


def test_hwf_a_flag_change_still_queued_holds_the_mark_so_the_row_is_read_again(app, world):
    from mailmind.bootstrap.mailbox import ingest
    from mailmind.bootstrap.mailbox import store
    name = "ada-inbox-02-lunch-confirm.eml"
    world.server.fail("LOGIN", user=w.ADA)            # so this one queues
    assert app.post("/api/flag", {**world.copy(name), "flag": "flagged", "on": True}).json["status"] == "queued"
    mark = modseq()
    with world.server.lock:
        world.server.mailbox(w.ADA, "INBOX").messages[world.uid(name)].flags.add("\\Seen")
    ingest.sync_new(acct())
    assert modseq() == mark
    c = store.conn()
    c.execute("UPDATE mail_commands SET state='error' WHERE account=? AND kind='flag'", (w.ADA,))
    c.commit()
    c.close()
    ingest.sync_new(acct())
    assert world.row(name)["seen"] == 1 and modseq() > mark


def test_hwf_a_new_uidvalidity_starts_the_mark_again(world):
    from mailmind.bootstrap.mailbox import ingest
    old = modseq()
    assert old
    world.server.renumber(w.ADA, "INBOX")
    ingest.sync_new(acct())
    assert modseq() == world.server.mailbox(w.ADA, "INBOX").highest_modseq


def test_hwf_a_sync_tells_an_open_list_of_mail_read_elsewhere(app, world, monkeypatch):
    from mailmind.bootstrap.mailbox import ingest
    from mailmind.platform import sse
    told = []
    real = sse.publish
    monkeypatch.setattr(sse, "publish", lambda topic, data=None: (told.append((topic, data)), real(topic, data))[1])
    ingest.sync_new(acct())
    assert [t for t, _ in told if t == "flags-changed"] == []         # nothing changed, nothing said
    with world.server.lock:
        world.server.mailbox(w.ADA, "INBOX").messages[world.uid("ada-inbox-02-lunch-confirm.eml")].flags.add(w.SEEN)
    ingest.sync_new(acct())
    assert [d for t, d in told if t == "flags-changed"] == [{"account": w.ADA, "folder": "INBOX", "count": 1}]
