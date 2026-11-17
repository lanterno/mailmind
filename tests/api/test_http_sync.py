"""What a sync keeps of new mail beyond its row, over HTTP: an Inbox message that carries an invite is stored with its
body, as opening it would store it, so Today & next has the invite without the message ever being opened; so is one
short enough for the first 64 KiB a sync reads of it to hold all of it, and a longer one waits for the body backfill or
to be opened. And when a folder is caught up as it opens: once a minute at most, never the Inbox, and at once after
MailMind put mail there. An account's first sync stores what its Inbox held before, which is no new mail."""
import json
from email.message import EmailMessage
from email.policy import SMTP
from urllib.parse import quote, urlencode

import pytest

from event_stream import Stream
from fixtures import world as w
from mailmind.bootstrap.mailbox import store

INVITE = "ada-inbox-06-outlook-invite.eml"
DEE = "dee@example.test"            # not one of the world's accounts


def rows(sql, *args):
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def row(uid, folder="INBOX"):
    [r] = rows("SELECT * FROM messages WHERE account=? AND folder=? AND uid=?", w.ADA, folder, uid)
    return r


def test_an_invite_that_arrives_is_kept_with_its_body(app, world):
    uid = world.deliver(w.ADA, "INBOX", w.variant(INVITE, Message_ID="<invite-again@example.test>"))
    new = row(uid)
    assert new["body_fetched"] == 1 and new["has_attach"] == 1
    assert [r["message_pk"] for r in rows("SELECT message_pk FROM bodies WHERE message_pk=?", new["pk"])] == [new["pk"]]
    assert rows("SELECT filename, content_type, is_inline FROM attachments WHERE message_pk=?", new["pk"]) == [
        {"filename": "invite.ics", "content_type": "text/calendar", "is_inline": 1}]
    events = [e for e in app.get("/api/upcoming").json["items"] if e.get("key") == new["message_key"]]
    assert events and all(e["folder"] == "INBOX" for e in events)


def with_pdf(message_id):
    """A note with a PDF of about 100 KiB: longer than the first 64 KiB of it a sync reads."""
    m = EmailMessage(policy=SMTP)
    m["From"], m["To"], m["Subject"] = "Lin Mori <lin@example.test>", "Ada Park <ada@example.test>", "The plans"
    m["Date"], m["Message-ID"] = "Fri, 30 Oct 2026 09:00:00 +0000", message_id
    m.set_content("The plans, attached.")
    m.add_attachment(b"%PDF-1.4\n" + bytes(100 * 1024), maintype="application", subtype="pdf", filename="plans.pdf")
    return m.as_bytes()


def found(app, words):
    return [r["uid"] for r in app.get(f"/api/search?scope=all&account={w.ADA}&q={quote(words)}").json["items"]
            if r["folder"] == "INBOX"]


def test_a_short_message_is_kept_whole_as_it_arrives_and_a_longer_one_waits_to_be_fetched(app, world):
    short = world.deliver(w.ADA, "INBOX", w.variant("ada-inbox-02-lunch-confirm.eml", Message_ID="<no-invite@example.test>")
                          .replace(b"See you there.", b"See you at the boathouse."))
    kept = row(short)
    assert kept["body_fetched"] == 1 and kept["snippet"] and "boathouse" in kept["body_text"]
    assert [r["message_pk"] for r in rows("SELECT message_pk FROM bodies WHERE message_pk=?", kept["pk"])] == [kept["pk"]]
    longer = world.deliver(w.ADA, "INBOX", with_pdf("<plans@example.test>"))
    waits = row(longer)
    assert waits["body_fetched"] == 0 and waits["snippet"] and waits["body_text"] is None
    assert rows("SELECT message_pk FROM bodies WHERE message_pk=?", waits["pk"]) == []
    # Search finds what the short one says at once.
    assert found(app, "boathouse") == [short]
    # Opened, the short one asks its server nothing, and the longer one is fetched whole, attachment and all.
    since = world.mark()
    copy = {"key": kept["message_key"], "folder": "INBOX"}
    assert "boathouse" in app.get("/api/message/body?" + urlencode(copy)).json["text"]
    assert [c for c in world.server.commands[since:] if c["command"] == "UID FETCH"] == []
    opened = app.get("/api/message/body?" + urlencode({"key": waits["message_key"], "folder": "INBOX"})).json
    assert [a["filename"] for a in opened["attachments"]] == ["plans.pdf"]
    assert row(longer)["body_fetched"] == 1


def test_an_invite_filed_elsewhere_waits_to_be_opened(app, world):
    uid = world.deliver(w.ADA, "Archive", w.variant(INVITE, Message_ID="<invite-filed@example.test>"))
    assert row(uid, "Archive")["body_fetched"] == 0


# ── a folder's catch-up as it opens ──────────────────────────────────────

CONFIRM = "ada-inbox-02-lunch-confirm.eml"
CLIENTS = "Clients"


def catch_ups(world, folder):
    """The searches above a folder's watermark: the catch-ups its opening ran."""
    return [c for c in world.server.commands_for(w.ADA, "UID SEARCH")
            if c["mailbox"] == folder and c["args"][:1] == ["UID"]]


def opened(app, folder):
    r = app.get(f"/api/messages?account={w.ADA}&folder={folder}")
    assert r.status == 200, r.json
    app.settle()                                 # the catch-up it set going has ended
    return r.json


def test_a_folder_opened_again_soon_is_caught_up_once(app, world):
    for _ in range(3):
        opened(app, "Archive")
    assert len(catch_ups(world, "Archive")) == 1


def test_opening_the_inbox_leaves_it_to_the_background_sync(app, world):
    opened(app, "INBOX")
    assert catch_ups(world, "INBOX") == []


def test_a_draft_saved_lets_drafts_catch_up_on_its_next_open(app, world):
    opened(app, "Drafts")
    assert app.post("/api/draft", {"account": w.ADA, "mode": "new", "to": ["lin@example.test"],
                                   "subject": "Swatch order", "text": "Two of each."}).json["ok"]
    opened(app, "Drafts")
    assert len(catch_ups(world, "Drafts")) == 2


@pytest.mark.parametrize("how", ["one", "conversation", "marked"])
def test_a_move_lets_its_folder_catch_up_on_its_next_open(app, world, how):
    world.server.add_folder(w.ADA, CLIENTS)
    world.server.deliver(w.ADA, CLIENTS, w.variant(CONFIRM, Message_ID="<filed-before@example.test>"))
    opened(app, CLIENTS)                         # its first scan
    opened(app, CLIENTS)                         # read just now: no catch-up
    assert catch_ups(world, CLIENTS) == []
    copy = world.copy(CONFIRM)
    path, body = {"one": ("/api/move", {**copy, "to": CLIENTS}),
                  "conversation": ("/api/thread/move", {**copy, "to": CLIENTS}),
                  "marked": ("/api/bulk/move", {"to": CLIENTS, "rows": [{**copy, "whole": False}]})}[how]
    assert app.post(path, body).status == 200
    app.work("commands")
    assert opened(app, CLIENTS)["items"]
    assert len(catch_ups(world, CLIENTS)) == 1


# ── an account's first sync ──────────────────────────────────────────────

def is_a(frame, topic):
    return f"\nevent: {topic}\n" in f"\n{frame}\n"


def heard_until(stream, topic):
    """What the stream said up to the first `topic` event, that one last."""
    said = [stream.event()]
    while not is_a(said[-1], topic):
        said.append(stream.event())
    return said


def test_an_accounts_first_sync_is_no_new_mail_and_what_comes_after_it_is(app, world):
    entry = world.server.add_account(DEE)
    for n in range(3):
        world.server.deliver(DEE, "INBOX", w.variant(CONFIRM, Message_ID=f"<dee-{n}@example.test>"))
    added = app.post("/api/accounts/add", {"email": DEE, "type": "imap", "host": entry["host"], "port": entry["port"],
                                            "starttls": entry["starttls"],
                                            "credential": {"style": "keychain", "value": "app-password"}})
    assert added.status == 200, added.json
    stream = Stream(app)
    stream.frame()

    app.post("/api/refresh", {})
    app.settle()
    # Muting a thread says so on the stream: everything the sync said comes before it.
    app.post("/api/mute", {"account": w.CY, "thread_id": world.row("cy-inbox-01-thread-start.eml")["thread_id"]})
    said = heard_until(stream, "mute")
    assert any(is_a(f, "sync-done") and DEE in f for f in said), said
    assert not any(is_a(f, "new-mail") for f in said), said
    assert app.get("/api/digest").json["sync"]["result"] == "0 new"
    assert len(rows("SELECT uid FROM messages WHERE account=? AND folder='INBOX'", DEE)) == 3

    world.server.deliver(DEE, "INBOX", w.variant(CONFIRM, Message_ID="<dee-after@example.test>"))
    app.post("/api/refresh", {})
    app.settle()
    arrived = heard_until(stream, "new-mail")[-1]
    assert DEE in arrived and '"count": 1' in arrived, arrived
    assert app.get("/api/digest").json["sync"]["result"] == "1 new"
    stream.close()


def test_a_first_sync_of_one_message_names_none_and_the_one_after_it_names_its_uid(app, world):
    # A first scan that stores one message is no new mail, so no notification names it; the one that comes after is, and
    # new-mail names its UID, so a notification can say whose it is and open it.
    entry = world.server.add_account(DEE)
    world.server.deliver(DEE, "INBOX", w.variant(CONFIRM, Message_ID="<dee-only@example.test>"))
    added = app.post("/api/accounts/add", {"email": DEE, "type": "imap", "host": entry["host"], "port": entry["port"],
                                            "starttls": entry["starttls"],
                                            "credential": {"style": "keychain", "value": "app-password"}})
    assert added.status == 200, added.json
    stream = Stream(app)
    stream.frame()

    app.post("/api/refresh", {})
    app.settle()
    app.post("/api/mute", {"account": w.CY, "thread_id": world.row("cy-inbox-01-thread-start.eml")["thread_id"]})
    said = heard_until(stream, "mute")
    assert any(is_a(f, "sync-done") and DEE in f for f in said), said
    assert not any(is_a(f, "new-mail") for f in said), said

    after = world.server.deliver(DEE, "INBOX", w.variant(CONFIRM, Message_ID="<dee-after-one@example.test>"))
    app.post("/api/refresh", {})
    app.settle()
    arrived = heard_until(stream, "new-mail")[-1]
    assert json.loads(arrived.split("data: ", 1)[1]) == {"account": DEE, "folder": "INBOX", "count": 1, "uid": after}
    stream.close()
