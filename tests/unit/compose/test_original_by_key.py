"""A reply or forward names its original by key (`original_key`), and a reopened draft its copy by key and folder: both
still name the same message after a renumber or an archive gives the row a new pk."""
from datetime import timedelta

from fixtures import world as w
from mailmind.bootstrap import accounts
from mailmind.bootstrap.compose import outbox
from mailmind.bootstrap.mailbox import ingest

LUNCH = "ada-inbox-02-lunch-confirm.eml"
SUBJECT = "Re: Lunch on Thursday?"


def reply(app, key, **kw):
    return app.post("/api/send", {"account": w.ADA, "mode": "reply", "original_key": key, "to": ["lin@example.test"],
                                  "subject": SUBJECT, "text": "See you.", "idempotency_key": "k-key", **kw})


def goes_out(app):
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()


def answered(world, name):
    return [(box, "\\Answered" in flags) for _, box, _, flags in world.where(name)["server"]]


def test_a_reply_names_its_original_by_key_after_a_renumber(app, world):
    key = world.key(LUNCH)
    world.server.renumber(w.ADA, "INBOX")
    ingest.sync_new(accounts.get(w.ADA))
    assert reply(app, key).json["ok"]
    goes_out(app)
    [sent] = world.smtp.messages
    assert w.message_id(LUNCH).encode() in sent.data
    assert answered(world, LUNCH) == [("INBOX", True)]


def test_a_reply_finds_its_original_by_key_once_it_is_archived(app, world):
    assert app.post("/api/archive", world.copy(LUNCH)).json["ok"]
    ingest.sync_folder(accounts.get(w.ADA), "Archive")                  # the copy it has now, once its folder is read
    assert reply(app, world.key(LUNCH)).json["ok"]
    goes_out(app)
    assert answered(world, LUNCH) == [("Archive", True)]


def test_a_reply_to_a_key_with_no_copy_left_is_not_found(app, world):
    r = reply(app, 987654)
    assert (r.status, r.json["detail"]) == (404, "original message not found")
    r = app.post("/api/draft", {"account": w.ADA, "mode": "forward", "original_key": 987654, "subject": "Fwd: x",
                                "text": "", "idempotency_key": "k-fwd"})
    assert (r.status, r.json["detail"]) == (404, "original message not found")


def test_a_reopened_draft_opens_by_key_and_folder_and_names_its_original_by_key(app, world):
    saved = app.post("/api/draft", {"account": w.ADA, "mode": "reply", "original_key": world.key(LUNCH),
                                    "to": ["lin@example.test"], "subject": SUBJECT, "text": "See you.",
                                    "idempotency_key": "cmp-1"}).json
    assert saved["ok"], saved
    [item] = [i for i in app.get(f"/api/messages?account={w.ADA}&folder=Drafts").json["items"] if i["subject"] == SUBJECT]
    opened = app.post("/api/draft/resume", {"key": item["key"], "folder": item["folder"]}).json
    assert opened["original_key"] == world.key(LUNCH) and "See you." in opened["text"]


def test_a_draft_no_longer_in_its_folder_does_not_reopen(app, world):
    r = app.post("/api/draft/resume", {"key": world.key("ada-drafts-01-thunderbird-v1.eml"), "folder": "INBOX"})
    assert (r.status, r.json["detail"]) == (404, "That draft is no longer in Drafts.")
