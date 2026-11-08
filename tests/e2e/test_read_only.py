"""The read-only profile writes nothing to a mail server or anywhere else outside (decision D4).

Moves, flag changes and sends wait in their queues, and a full run applies them. What can't wait
there (a draft, a sweep, an unsubscribe) is refused, with a 409 that says why. Below that, a read-only
process's IMAP connections open every mailbox with EXAMINE and refuse any command that could
change mail before it is sent, and it opens no SMTP session. Each test runs read-only through
MAILMIND_PROFILE until it calls `read_only.end()`, and checks what the fake servers saw.
"""
from datetime import timedelta

import pytest

import problems
from fixtures import world as w
from mailmind.platform.profile import ReadOnlyError
from mailmind.bootstrap.curation import safe_http

RECEIPT = "ada-inbox-05-receipt.eml"
SCAM = "ada-inbox-12-scam.eml"
NEWSLETTER = "ada-inbox-03-newsletter.eml"
CI = "ada-inbox-04-ci.eml"
LUNCH_CONFIRM = "ada-inbox-02-lunch-confirm.eml"
LOOM = "news@loom.example.test"
REFUSED = problems.read_only()


@pytest.fixture
def read_only(monkeypatch):
    """This test runs read-only; read_only.end() makes it full from there on."""
    monkeypatch.delenv("MAILMIND_NOSYNC", raising=False)
    monkeypatch.setenv("MAILMIND_PROFILE", "read-only")

    class ReadOnly:
        @staticmethod
        def end():
            monkeypatch.setenv("MAILMIND_PROFILE", "full")

        @staticmethod
        def again():
            monkeypatch.setenv("MAILMIND_PROFILE", "read-only")
            return ReadOnly.end
    return ReadOnly()


def q(sql, *args):
    from mailmind.bootstrap.mailbox import store
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
    from mailmind.bootstrap import accounts
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
    from mailmind.bootstrap import accounts
    M = accounts.connect(accounts.get(w.ADA))
    try:
        assert isinstance(M, imaplib.IMAP4)
    finally:
        M.logout()


def test_it_opens_no_smtp_session(world, read_only):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.compose import transport
    before = len(world.smtp.commands)
    with pytest.raises(ReadOnlyError):
        transport.send(accounts.get(w.ADA), b"Subject: x\r\n\r\nx\r\n", ["lin@example.test"])
    assert len(world.smtp.commands) == before and world.smtp.messages == []


# -- what waits in a queue ----------------------------------------------------

def test_moves_wait_in_the_queue_until_a_full_run_applies_them(app, world, read_only):
    from mailmind.bootstrap.mailbox import commands
    since = world.mark()
    for path, name, extra in (("/api/archive", RECEIPT, {}), ("/api/trash", SCAM, {}),
                              ("/api/junk", NEWSLETTER, {}), ("/api/archive", CI, {})):
        r = app.post(path, {**world.copy(name), "mutation_id": f"m-{name}", **extra}).json
        assert (r["ok"], r["status"]) == (True, "queued"), (path, r)
    assert app.post("/api/thread/archive", world.copy(LUNCH_CONFIRM)).json["ok"]
    commands.drain()                                                   # a read-only drain does nothing
    assert world.writes(since) == []
    assert {r["state"] for r in q("SELECT state FROM mail_commands")} == {"queued"}
    assert q("SELECT max(attempts) n FROM mail_commands") == [{"n": 0}]
    assert on_server(world, RECEIPT) == [(w.ADA, "INBOX")]
    listed = [{"key": m["key"], "folder": m["folder"]} for m in app.get(f"/api/messages?account={w.ADA}&folder=INBOX").json["items"]]
    assert not [n for n in (RECEIPT, SCAM, NEWSLETTER, CI, LUNCH_CONFIRM) if world.copy(n) in listed]

    read_only.end()
    commands.drain()
    assert {r["state"] for r in q("SELECT state FROM mail_commands")} == {"done"}
    assert on_server(world, RECEIPT) == [(w.ADA, "Archive")]
    assert on_server(world, SCAM) == [(w.ADA, "Trash")]
    assert on_server(world, NEWSLETTER) == [(w.ADA, "Junk")]
    assert on_server(world, CI) == [(w.ADA, "Archive")]
    assert (w.ADA, "INBOX") not in on_server(world, LUNCH_CONFIRM)


def test_undo_takes_a_waiting_move_back_without_a_write(app, world, read_only):
    since = world.mark()
    assert app.post("/api/archive", world.copy(RECEIPT)).json["status"] == "queued"
    assert app.post("/api/undo", {}).json["ok"]
    assert q("SELECT id FROM mail_commands") == []
    assert world.writes(since) == []
    assert [(d["account"], d["folder"]) for d in world.where(RECEIPT)["db"]] == [(w.ADA, "INBOX")]


def test_a_send_waits_in_the_outbox_until_a_full_run_sends_it(app, world, read_only):
    from mailmind.bootstrap.compose import outbox
    oid = app.post("/api/send", send(idempotency_key="k-wait")).json["outbox_id"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    assert q("SELECT state, attempts FROM outbox WHERE id=?", oid) == [{"state": "queued", "attempts": 0}]
    assert world.smtp.messages == []

    read_only.end()
    outbox.drain()
    assert q("SELECT state FROM outbox WHERE id=?", oid) == [{"state": "sent"}]
    assert [m.rcpts for m in world.smtp.messages] == [["lin@example.test"]]


def test_a_flag_change_waits_in_the_queue_until_a_full_run_applies_it(app, world, read_only):
    from mailmind.bootstrap.mailbox import commands
    since = world.mark()
    for path, body in (("/api/seen", {"seen": True}), ("/api/flag", {"flag": "flagged", "on": True})):
        r = app.post(path, {**world.copy(RECEIPT), **body})
        assert (r.status, r.json["status"]) == (200, "queued"), path
    commands.drain()                                                   # a read-only drain does nothing
    assert world.writes(since) == []
    assert {k: world.where(RECEIPT)["db"][0][k] for k in ("seen", "flagged")} == {"seen": 1, "flagged": 1}

    read_only.end()
    commands.drain()
    assert {r["state"] for r in q("SELECT state FROM mail_commands")} == {"done"}
    [(_, _, _, flags)] = world.where(RECEIPT)["server"]
    assert {w.SEEN, "\\Flagged"} <= set(flags)


# -- what is refused ----------------------------------------------------------


def test_a_draft_waits_here_until_a_full_run_pushes_it(app, world, read_only):
    from mailmind.bootstrap.compose import drafts
    since = world.mark()
    r = app.post("/api/draft", send(subject="Fabric order (draft)"))
    assert r.status == 200 and r.json["pushed"] is False
    assert [d["subject"] for d in app.get("/api/outbox").json["drafts"]] == ["Fabric order (draft)"]
    assert world.writes(since) == []
    read_only.end()
    drafts.push_round()
    assert world.server.find(w.ADA, "Drafts", r.json["message_id"])
    assert app.get("/api/outbox").json["drafts"] == []


def test_a_discard_waits_too(app, world, read_only):
    from mailmind.bootstrap.compose import drafts
    read_only.end()
    saved = app.post("/api/draft", send(subject="Fabric order (draft)")).json
    monkey_env = read_only.again()
    since = world.mark()
    r = app.post("/api/draft", {"delete": True, "draft_id": saved["draft_id"]})
    assert r.status == 200 and r.json["deleted"] is True
    assert world.writes(since) == [] and world.server.find(w.ADA, "Drafts", saved["message_id"])
    monkey_env()
    drafts.push_round()
    assert not world.server.find(w.ADA, "Drafts", saved["message_id"])


def test_a_sweep_is_refused_but_its_dry_run_still_plans(app, world, read_only):
    since = world.mark()
    r = app.post("/api/sweep", {"account": w.ADA})
    assert (r.status, r.json) == (409, REFUSED)
    r = app.post("/api/sweep", {"account": w.ADA, "dry_run": True})
    assert r.status == 200 and r.json["dry_run"] is True
    assert world.writes(since) == []


def test_a_cleanup_blocks_and_queues_its_moves_but_sends_no_unsubscribe(app, world, read_only, monkeypatch):
    posts = []
    monkeypatch.setattr(safe_http, "one_click_post", lambda url, _redirects=1: posts.append(url))
    mid = "<issue-219@loom.example.test>"                             # one-click, filed where the sweep can't reach
    world.deliver(w.ADA, "Archive", w.variant(NEWSLETTER, Message_ID=mid, List_Unsubscribe_Post="List-Unsubscribe=One-Click",
                                              Date="Mon, 02 Nov 2026 07:00:00 +0000"), (w.SEEN,))
    [archived] = q("SELECT message_key, folder FROM messages WHERE message_id=?", mid)
    assert app.get("/api/message/body?key={message_key}&folder={folder}".format(**archived)).status == 200
    plan = app.get(f"/api/cleanup/sender?addr={LOOM}").json
    assert plan["unsub"]["method"] == "one-click"
    since = world.mark()
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True,
                                          "confirm_token": plan["confirm_token"]}).json
    assert (r["ok"], r["status"], r["sweep_queued"]) == (True, "read_only", 1)
    assert posts == [] and world.smtp.messages == [] and world.writes(since) == []
    assert q("SELECT * FROM unsub_log") == []                          # not latched: a full run can still send it


def test_the_unsubscribe_request_itself_refuses(read_only):
    with pytest.raises(ReadOnlyError):
        safe_http.one_click_post("https://loom.example.test/unsubscribe")


def test_the_digest_names_the_profile_the_page_shows(app, world, read_only):
    assert app.get("/api/digest").json["profile"] == "read-only"
    read_only.end()
    assert app.get("/api/digest").json["profile"] == "full"
