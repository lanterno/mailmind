"""What a sync keeps of new mail beyond its row, over HTTP: an Inbox message that carries an invite is stored with its
body, as opening it would store it, so Today & next has the invite without the message ever being opened. And when a
folder is caught up as it opens: once a minute at most, never the Inbox, and at once after MailMind put mail there."""
import pytest

from fixtures import world as w
from mailmind.bootstrap.mailbox import store

INVITE = "ada-inbox-06-outlook-invite.eml"


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


def test_mail_with_no_invite_waits_to_be_opened(app, world):
    uid = world.deliver(w.ADA, "INBOX", w.variant("ada-inbox-02-lunch-confirm.eml", Message_ID="<no-invite@example.test>"))
    new = row(uid)
    assert new["body_fetched"] == 0 and new["snippet"]
    assert rows("SELECT message_pk FROM bodies WHERE message_pk=?", new["pk"]) == []


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
