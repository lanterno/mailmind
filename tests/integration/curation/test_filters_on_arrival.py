"""A filter at ingest: new Inbox mail that fits is starred or marked read through the command path, and nothing else is touched.
What the filters ask of one message, and that one which can't be judged stops no sync, is tests/unit/curation/test_filters_on_arrival.py."""
from fixtures import world as w
from mailmind.bootstrap import accounts
from mailmind.bootstrap.mailbox import store
from mailmind.bootstrap import mailbox

LUNCH = "ada-inbox-02-lunch-confirm.eml"
NEWS = "ada-inbox-03-newsletter.eml"
SCAM = "ada-inbox-12-scam.eml"


def filter_on(app, q, star=False, read=False):
    r = app.post("/api/filters", {"verb": "create", "q": q, "star": star, "read": read})
    assert r.status == 200, r.json


def a_lunch(n, **headers):
    return w.variant(LUNCH, Message_ID=f"<filter-{n}@example.test>", **headers)


def stored(uid, folder="INBOX"):
    c = store.conn()
    try:
        return c.execute("SELECT * FROM messages WHERE account=? AND folder=? AND uid=?", (w.ADA, folder, uid)).fetchone()
    finally:
        c.close()


def on_server(world, uid, folder="INBOX"):
    return world.server.messages(w.ADA, folder)[uid].flags


def waiting():
    c = store.conn()
    try:
        return [r["params"] for r in c.execute("SELECT params FROM mail_commands WHERE kind='flag' AND state='queued'")]
    finally:
        c.close()


def test_new_mail_that_fits_is_starred_and_the_server_hears(app, world):
    filter_on(app, "from:lin@example.test", star=True)
    uid = world.deliver(w.ADA, "INBOX", a_lunch(1))
    assert stored(uid)["flagged"] == 1                                       # here at once, in the sync
    assert w.FLAGGED not in on_server(world, uid)
    mailbox.commands.drain()
    assert w.FLAGGED in on_server(world, uid)
    assert w.SEEN not in on_server(world, uid) and stored(uid)["seen"] == 0


def test_mark_read_reaches_the_server(app, world):
    filter_on(app, "from:lin@example.test", read=True)
    uid = world.deliver(w.ADA, "INBOX", a_lunch(1))
    assert stored(uid)["seen"] == 1
    mailbox.commands.drain()
    assert w.SEEN in on_server(world, uid) and stored(uid)["flagged"] == 0


def test_two_filters_are_one_change(app, world):
    filter_on(app, "from:lin@example.test", star=True)
    filter_on(app, "subject:lunch", read=True)
    uid = world.deliver(w.ADA, "INBOX", a_lunch(1))
    assert len(waiting()) == 1
    mailbox.commands.drain()
    assert {w.SEEN, w.FLAGGED} <= set(on_server(world, uid))


def test_words_are_found_in_the_message_just_stored(app, world):
    filter_on(app, "thursday", star=True)
    filter_on(app, "absent-word-nowhere", read=True)
    uid = world.deliver(w.ADA, "INBOX", a_lunch(1))
    row = stored(uid)
    assert row["flagged"] == 1 and row["seen"] == 0


def test_a_negated_clause_counts(app, world):
    filter_on(app, "from:lin@example.test -subject:thursday", star=True)
    uid = world.deliver(w.ADA, "INBOX", a_lunch(1))
    assert stored(uid)["flagged"] == 0
    assert waiting() == []


def test_a_kind_of_mail_can_be_named(app, world):
    filter_on(app, "is:newsletter", read=True)
    uid = world.deliver(w.ADA, "INBOX", w.variant(NEWS, Message_ID="<filter-news@loom.example.test>"))
    assert stored(uid)["seen"] == 1


def test_a_filter_can_mark_junk_read(app, world):
    filter_on(app, "from:winner@prizes.example.test", read=True)
    uid = world.deliver(w.ADA, "INBOX", w.variant(SCAM, Message_ID="<filter-scam@prizes.example.test>"))
    row = stored(uid)
    assert row["is_junk"] == 1 and row["seen"] == 1


def test_mail_that_doesnt_fit_asks_nothing(app, world):
    filter_on(app, "from:someone-else@example.test", star=True)
    since = world.mark()
    uid = world.deliver(w.ADA, "INBOX", a_lunch(1))
    assert stored(uid)["flagged"] == 0 and waiting() == []
    mailbox.commands.drain()
    assert world.writes(since) == []


def test_flags_it_came_with_ask_nothing(app, world):
    filter_on(app, "from:lin@example.test", star=True, read=True)
    since = world.mark()
    uid = world.deliver(w.ADA, "INBOX", a_lunch(1), flags=(w.SEEN, w.FLAGGED))
    assert waiting() == []
    mailbox.commands.drain()
    assert world.writes(since) == [] and stored(uid)["flagged"] == 1


def test_only_what_it_lacks_is_asked(app, world):
    filter_on(app, "from:lin@example.test", star=True, read=True)
    world.deliver(w.ADA, "INBOX", a_lunch(1), flags=(w.SEEN,))
    assert len(waiting()) == 1 and '"flags": {"flagged": true}' in waiting()[0]


def test_another_folder_isnt_arrival(app, world):
    filter_on(app, "from:lin@example.test", star=True)
    uid = world.deliver(w.ADA, "Archive", a_lunch(1))
    assert stored(uid, "Archive")["flagged"] == 0 and waiting() == []


def test_a_whole_scan_reads_what_was_already_there(app, world):
    filter_on(app, "from:lin@example.test", star=True)
    uid = world.server.deliver(w.ADA, "INBOX", a_lunch(1))
    mailbox.ingest.sync_folder(accounts.get(w.ADA), "INBOX", full=True)
    assert stored(uid)["flagged"] == 0 and waiting() == []


def test_mail_seen_before_isnt_new(app, world):
    uid = world.deliver(w.ADA, "Archive", a_lunch(1))
    assert stored(uid, "Archive")["flagged"] == 0
    filter_on(app, "from:lin@example.test", star=True)
    back = world.deliver(w.ADA, "INBOX", a_lunch(1))                         # moved in by another client
    assert stored(back)["flagged"] == 0 and waiting() == []


def test_a_message_brought_back_by_undo_isnt_new(app, world):
    uid = world.deliver(w.ADA, "INBOX", a_lunch(1))
    filter_on(app, "from:lin@example.test", star=True)
    key = stored(uid)["message_key"]
    assert app.post("/api/archive", {"key": key, "folder": "INBOX", "uid": uid}).status == 200
    mailbox.commands.drain()
    assert app.post("/api/undo", {}).status == 200
    mailbox.commands.drain()
    c = store.conn()
    try:
        rows = c.execute("SELECT * FROM messages WHERE account=? AND message_key=?", (w.ADA, key)).fetchall()
    finally:
        c.close()
    assert rows and all(r["flagged"] == 0 for r in rows) and waiting() == []


def test_a_later_sync_doesnt_take_the_mark_back(app, world):
    filter_on(app, "from:lin@example.test", star=True)
    uid = world.deliver(w.ADA, "INBOX", a_lunch(1))
    mailbox.ingest.sync_new(accounts.get(w.ADA))                                     # the server still says unflagged
    assert stored(uid)["flagged"] == 1 and len(waiting()) == 1


def test_read_only_marks_it_here_and_the_server_waits(app, world, monkeypatch):
    filter_on(app, "from:lin@example.test", star=True)
    uid = world.server.deliver(w.ADA, "INBOX", a_lunch(1))
    monkeypatch.setenv("MAILMIND_PROFILE", "read-only")
    since = world.mark()
    mailbox.ingest.sync_new(accounts.get(w.ADA))
    mailbox.commands.drain()
    assert world.writes(since) == []
    assert stored(uid)["flagged"] == 1 and len(waiting()) == 1


def test_the_worker_is_woken(app, world, monkeypatch):
    woken = []
    monkeypatch.setattr(mailbox.commands, "kick", lambda: woken.append(1))
    filter_on(app, "from:lin@example.test", star=True)
    world.deliver(w.ADA, "INBOX", a_lunch(1))
    assert woken
