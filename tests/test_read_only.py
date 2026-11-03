"""The read-only profile writes nothing to a mail server or anywhere else outside (decision D4).

Moves and sends wait in their queues, and a full run applies them. What can't wait there (a flag,
a draft, a sweep, an unsubscribe) is refused, with a 409 that says why. Below that, a read-only
process's IMAP connections open every mailbox with EXAMINE and refuse any command that could
change mail before it is sent, and it opens no SMTP session. Each test runs read-only through
MAILMIND_PROFILE until it calls `read_only.end()`, and checks what the fake servers saw.
"""
from datetime import timedelta

import pytest

from fixtures import world as w
from mailmind.platform.profile import ReadOnlyError

RECEIPT = "ada-inbox-05-receipt.eml"
SCAM = "ada-inbox-12-scam.eml"
NEWSLETTER = "ada-inbox-03-newsletter.eml"
CI = "ada-inbox-04-ci.eml"
LUNCH_CONFIRM = "ada-inbox-02-lunch-confirm.eml"
LOOM = "news@loom.example.test"
REFUSED = {"ok": False, "error": "MailMind is running read-only", "read_only": True}


@pytest.fixture
def read_only(monkeypatch):
    """This test runs read-only; read_only.end() makes it full from there on."""
    monkeypatch.delenv("MAILMIND_NOSYNC", raising=False)
    monkeypatch.setenv("MAILMIND_PROFILE", "read-only")

    class ReadOnly:
        @staticmethod
        def end():
            monkeypatch.setenv("MAILMIND_PROFILE", "full")
    return ReadOnly()


def q(sql, *args):
    import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def on_server(world, name):
    return [(acct, box) for acct, box, _uid, _flags in world.where(name)["server"]]


def send(**kw):
    return {"account": w.ADA, "mode": "new", "to": ["Lin Mori <lin@example.test>"], "subject": "Fabric order",
            "text": "Hi Lin,\n\nThe linen came in. Want half?\n\nAda", **kw}


# -- the connections -----------------------------------------------------------

def test_its_imap_connection_examines_and_refuses_every_write_before_sending_it(world, read_only):
    import accounts
    since = world.mark()
    M = accounts.connect(accounts.get(w.ADA))
    try:
        assert isinstance(M, accounts.ReadOnlyIMAP)
        assert M.select('"INBOX"', readonly=False)[0] == "OK"            # asked read-write, opened read-only
        assert M.uid("SEARCH", None, "ALL")[0] == "OK"
        assert M.uid("FETCH", str(world.uid(RECEIPT)), "(FLAGS)")[0] == "OK"
        assert M.list()[0] == "OK" and M.noop()[0] == "OK" and M.status('"INBOX"', "(MESSAGES)")[0] == "OK"
        uid = str(world.uid(RECEIPT))
        writes = {"UID STORE": lambda: M.uid("STORE", uid, "+FLAGS", "(\\Seen)"),
                  "UID MOVE": lambda: M.uid("MOVE", uid, '"Trash"'),
                  "UID COPY": lambda: M.uid("COPY", uid, '"Trash"'),
                  "UID EXPUNGE": lambda: M.uid("EXPUNGE", uid),
                  "append": lambda: M.append('"Drafts"', "", None, b"Subject: x\r\n\r\nx\r\n"),
                  "expunge": lambda: M.expunge(), "store": lambda: M.store("1", "+FLAGS", "(\\Seen)"),
                  "copy": lambda: M.copy("1", '"Trash"'), "create": lambda: M.create('"New"'),
                  "delete": lambda: M.delete('"Junk"'), "rename": lambda: M.rename('"Junk"', '"Spam"'),
                  "xatom": lambda: M.xatom("XAPPLY")}
        for name, write in writes.items():
            with pytest.raises(ReadOnlyError, match=f"^IMAP {name}$"):
                write()
    finally:
        M.logout()
    sent = [c["command"] for c in world.server.commands[since:]]
    assert "EXAMINE" in sent and "SELECT" not in sent
    assert world.writes(since) == []


def test_a_full_process_gets_the_connection_itself(world):
    import imaplib
    import accounts
    M = accounts.connect(accounts.get(w.ADA))
    try:
        assert isinstance(M, imaplib.IMAP4)
    finally:
        M.logout()


def test_it_opens_no_smtp_session(world, read_only):
    import accounts
    import smtpsend
    before = len(world.smtp.commands)
    with pytest.raises(ReadOnlyError):
        smtpsend.send(accounts.get(w.ADA), b"Subject: x\r\n\r\nx\r\n", ["lin@example.test"])
    assert len(world.smtp.commands) == before and world.smtp.messages == []


# -- what waits in a queue ----------------------------------------------------

def test_moves_wait_in_the_queue_until_a_full_run_applies_them(app, world, read_only):
    import readmutq
    since = world.mark()
    for path, name, extra in (("/api/archive", RECEIPT, {}), ("/api/trash", SCAM, {}),
                              ("/api/junk", NEWSLETTER, {}), ("/api/move", CI, {"dest": "Archive"})):
        r = app.post(path, {"pk": world.pk(name), "mutation_id": f"m-{name}", **extra}).json
        assert (r["ok"], r["status"]) == (True, "queued"), (path, r)
    assert app.post("/api/thread/archive", {"pk": world.pk(LUNCH_CONFIRM)}).json["ok"]
    readmutq.drain()                                                   # a read-only drain does nothing
    assert world.writes(since) == []
    assert {r["state"] for r in q("SELECT state FROM read_mutations")} == {"queued"}
    assert q("SELECT max(attempts) n FROM read_mutations") == [{"n": 0}]
    assert on_server(world, RECEIPT) == [(w.ADA, "INBOX")]
    listed = {m["pk"] for m in app.get(f"/api/messages?account={w.ADA}&folder=INBOX").json["items"]}
    assert not listed & {world.pk(n) for n in (RECEIPT, SCAM, NEWSLETTER, CI, LUNCH_CONFIRM)}

    read_only.end()
    readmutq.drain()
    assert {r["state"] for r in q("SELECT state FROM read_mutations")} == {"done"}
    assert on_server(world, RECEIPT) == [(w.ADA, "Archive")]
    assert on_server(world, SCAM) == [(w.ADA, "Trash")]
    assert on_server(world, NEWSLETTER) == [(w.ADA, "Junk")]
    assert on_server(world, CI) == [(w.ADA, "Archive")]
    assert (w.ADA, "INBOX") not in on_server(world, LUNCH_CONFIRM)


def test_undo_takes_a_waiting_move_back_without_a_write(app, world, read_only):
    since = world.mark()
    assert app.post("/api/archive", {"pk": world.pk(RECEIPT)}).json["status"] == "queued"
    assert app.post("/api/undo", {}).json["ok"]
    assert q("SELECT id FROM read_mutations") == []
    assert world.writes(since) == []
    assert [(d["account"], d["folder"]) for d in world.where(RECEIPT)["db"]] == [(w.ADA, "INBOX")]


def test_a_send_waits_in_the_outbox_until_a_full_run_sends_it(app, world, read_only):
    import outbox
    oid = app.post("/api/send", send(idempotency_key="k-wait")).json["outbox_id"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    assert q("SELECT state, attempts FROM outbox WHERE id=?", oid) == [{"state": "queued", "attempts": 0}]
    assert world.smtp.messages == []

    read_only.end()
    outbox.drain()
    assert q("SELECT state FROM outbox WHERE id=?", oid) == [{"state": "sent"}]
    assert [m.rcpts for m in world.smtp.messages] == [["lin@example.test"]]


# -- what is refused ----------------------------------------------------------

def test_a_flag_change_is_refused(app, world, read_only):
    since = world.mark()
    for path, body in (("/api/seen", {"seen": True}), ("/api/flag", {"flag": "flagged", "on": True})):
        r = app.post(path, {"pk": world.pk(RECEIPT), **body})
        assert (r.status, r.json) == (409, REFUSED), path
    assert world.writes(since) == []
    assert world.where(RECEIPT)["db"][0]["seen"] == 0


def test_a_draft_is_neither_saved_nor_deleted(app, world, read_only):
    since = world.mark()
    r = app.post("/api/draft", send(subject="Fabric order (draft)"))
    assert (r.status, r.json) == (409, REFUSED)
    r = app.post("/api/draft", {"delete": True, "draft_id": 1})
    assert (r.status, r.json) == (409, REFUSED)
    assert world.writes(since) == []


def test_a_sweep_is_refused_but_its_dry_run_still_plans(app, world, read_only):
    since = world.mark()
    r = app.post("/api/sweep", {"account": w.ADA})
    assert (r.status, r.json) == (409, REFUSED)
    r = app.post("/api/sweep", {"account": w.ADA, "dry_run": True})
    assert r.status == 200 and r.json["dry_run"] is True
    assert world.writes(since) == []


def test_a_cleanup_blocks_and_queues_its_moves_but_sends_no_unsubscribe(app, world, read_only, monkeypatch):
    import server
    posts = []
    monkeypatch.setattr(server, "_unsub_oneclick_post", lambda url, _redirects=1: posts.append(url))
    mid = "<issue-219@loom.example.test>"                             # one-click, filed where the sweep can't reach
    world.deliver(w.ADA, "Archive", w.variant(NEWSLETTER, Message_ID=mid, List_Unsubscribe_Post="List=One-Click",
                                              Date="Mon, 02 Nov 2026 07:00:00 +0000"), (w.SEEN,))
    [archived] = q("SELECT pk FROM messages WHERE message_id=?", mid)
    assert app.get(f"/api/message/{archived['pk']}/body").status == 200
    plan = app.get(f"/api/cleanup/sender?addr={LOOM}").json
    assert plan["unsub"]["method"] == "one-click"
    since = world.mark()
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True,
                                          "confirm_token": plan["confirm_token"]}).json
    assert (r["ok"], r["status"], r["sweep_queued"]) == (True, "read_only", 1)
    assert posts == [] and world.smtp.messages == [] and world.writes(since) == []
    assert q("SELECT * FROM unsub_log") == []                          # not latched: a full run can still send it


def test_the_unsubscribe_request_itself_refuses(read_only):
    import server
    with pytest.raises(ReadOnlyError):
        server._unsub_oneclick_post("https://loom.example.test/unsubscribe")
