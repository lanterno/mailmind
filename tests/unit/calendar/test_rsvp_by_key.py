"""An RSVP names its invite by key, which still names it after a renumber gives its row a new pk, and Today & next says
which copy each event came from."""
from fixtures import world as w
from mailmind.bootstrap import accounts
from mailmind.bootstrap.mailbox import ingest

INVITE = "ada-inbox-06-outlook-invite.eml"


def test_an_invite_is_answered_by_its_key_after_a_renumber(app, world):
    key = world.key(INVITE)
    world.server.renumber(w.ADA, "INBOX")
    ingest.sync_new(accounts.get(w.ADA))
    r = app.post("/api/invite/rsvp", {"key": key, "folder": "INBOX", "decision": "accept"})
    assert r.status == 200 and r.json["queued"] is True


def test_an_invite_with_no_copy_left_is_not_found(app, world):
    r = app.post("/api/invite/rsvp", {"key": 987654, "decision": "accept"})
    assert (r.status, r.json["detail"]) == (404, "not found")


def test_each_event_names_its_message_and_copy(app, world):
    events = [e for e in app.get("/api/upcoming").json["items"] if e.get("key") == world.key(INVITE)]
    assert events and all(e["folder"] == "INBOX" for e in events)
