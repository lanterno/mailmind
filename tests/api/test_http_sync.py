"""What a sync keeps of new mail beyond its row, over HTTP: an Inbox message that carries an invite is stored with its
body, as opening it would store it, so Today & next has the invite without the message ever being opened."""
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
