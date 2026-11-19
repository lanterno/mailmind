"""Reply on a message that already has a reply saved to it opens that reply, with its words, never a second, empty one.

The message as it opens (`/api/message/body`) names the newest draft that answers it (`reply_draft`): MailMind's own by its
id, wherever its newest save is, and another app's by its copy in Drafts, with how many more answer it. `/api/draft/resume`
opens either. A save kept on this Mac opens from the journal; one that has reached Drafts opens from its copy there, listed
first if Drafts hasn't been caught up since, as after a restart. A send or a discard leaves the message with no reply
waiting, and so does a copy another app deleted.
"""
from datetime import timedelta
from urllib.parse import urlencode

from fixtures import world as w

LUNCH = "ada-inbox-01-lunch.eml"
CONTRACT = "bea-inbox-01-client.eml"          # Bea's Apple Mail draft in Drafts answers it


def reply(world, **kw):
    return {"account": w.ADA, "mode": "reply", "original_key": world.key(LUNCH), "to": ["lin@example.test"],
            "subject": "Re: Lunch on Thursday?", "text": "Thursday suits me.", "idempotency_key": "box-1", **kw}


def save(app, body):
    r = app.post("/api/draft", body)
    assert r.status == 200 and r.json["ok"], r.json
    return r.json


def waiting(app, world, name=LUNCH):
    """What the message says, as it opens, of a reply saved to it: None for none."""
    r = app.get("/api/message/body?" + urlencode(world.copy(name)))
    assert r.status == 200, r.json
    return r.json.get("reply_draft")


def reopen(app, found):
    """The draft a message names, opened as a client opens it: MailMind's own by its id, another app's by its copy."""
    return app.post("/api/draft/resume", {"draft_id": found["draft_id"]} if found["draft_id"] else found["in_drafts"])


def drafts_on_the_server(world, account=w.ADA):
    with world.server.lock:
        box = world.server.mailbox(account, "Drafts")
        return {uid: m for uid, m in box.messages.items() if "\\Deleted" not in m.flags and b"Thursday suits" in m.raw}


def listed_in_drafts():
    """The copies of the reply the database lists in Ada's Drafts."""
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return c.execute("SELECT count(*) FROM messages WHERE account=? AND folder='Drafts' AND subject=?",
                         (w.ADA, "Re: Lunch on Thursday?")).fetchone()[0]
    finally:
        c.close()


def test_a_message_no_draft_answers_names_none(app, world):
    assert waiting(app, world) is None


def test_a_reply_kept_on_this_mac_opens_from_the_journal(app, world):
    did = save(app, reply(world, autosave=True))["draft_id"]
    assert drafts_on_the_server(world) == {}
    found = waiting(app, world)
    assert found == {"draft_id": did, "in_drafts": None, "more": 0}
    r = reopen(app, found)
    assert r.status == 200, r.json
    assert (r.json["draft_id"], r.json["mode"], r.json["text"].startswith("Thursday suits me.")) == (did, "reply", True)
    assert r.json["original_key"] == world.key(LUNCH)


def test_a_reply_that_reached_drafts_opens_after_a_restart_though_drafts_was_never_listed_since(app, world):
    saved = save(app, reply(world))
    assert saved["pushed"] is True and len(drafts_on_the_server(world)) == 1
    app.restart()
    found = waiting(app, world)
    assert found == {"draft_id": saved["draft_id"], "in_drafts": None, "more": 0}
    assert listed_in_drafts() == 0
    r = reopen(app, found)
    assert r.status == 200, r.json
    assert (r.json["draft_id"], r.json["text"].startswith("Thursday suits me.")) == (saved["draft_id"], True)
    # It opened from its copy, which Drafts now lists, and the copy stays the one draft.
    assert listed_in_drafts() == 1 and len(drafts_on_the_server(world)) == 1
    assert waiting(app, world) == found


def test_a_reply_listed_in_drafts_opens_from_its_copy(app, world):
    saved = save(app, reply(world))
    app.work("sync", w.ADA, "Drafts")
    assert listed_in_drafts() == 1
    found = waiting(app, world)
    assert found == {"draft_id": saved["draft_id"], "in_drafts": None, "more": 0}
    r = reopen(app, found)
    assert r.status == 200, r.json
    assert r.json["text"].startswith("Thursday suits me.")


def test_a_reply_saved_again_opens_with_its_newest_words(app, world):
    first = save(app, reply(world))
    app.clock.shift(timedelta(minutes=2))
    save(app, reply(world, draft_id=first["draft_id"], base_version=first["version"], text="Friday, then.", autosave=True))
    found = waiting(app, world)
    assert found == {"draft_id": first["draft_id"], "in_drafts": None, "more": 0}
    assert reopen(app, found).json["text"].startswith("Friday, then.")


def test_another_apps_reply_in_drafts_is_named_by_its_copy(app, world):
    found = waiting(app, world, CONTRACT)
    assert found == {"draft_id": None, "in_drafts": world.copy("bea-drafts-01-apple-mail.eml"), "more": 0}
    r = reopen(app, found)
    assert r.status == 200, r.json
    assert r.json["mode"] == "reply" and r.json["original_key"] == world.key(CONTRACT)


def test_of_two_replies_the_newest_opens_and_the_other_is_counted(app, world):
    older = save(app, reply(world, autosave=True))["draft_id"]
    app.clock.shift(timedelta(minutes=5))
    newer = save(app, reply(world, idempotency_key="box-2", text="Or Friday?", autosave=True))["draft_id"]
    assert older != newer
    assert waiting(app, world) == {"draft_id": newer, "in_drafts": None, "more": 1}


def test_a_reply_sent_leaves_none_waiting(app, world):
    saved = save(app, reply(world))
    r = app.post("/api/send", reply(world, draft_id=saved["draft_id"], idempotency_key="send-1"))
    assert r.status == 200 and r.json["ok"], r.json
    assert waiting(app, world) is None                 # queued: the send holds it
    app.clock.shift(timedelta(seconds=r.json["undo_seconds"] + 1))
    app.work("outbox")
    assert drafts_on_the_server(world) == {}
    assert waiting(app, world) is None


def test_a_reply_discarded_leaves_none_waiting(app, world):
    kept = save(app, reply(world, autosave=True))["draft_id"]
    pushed = save(app, reply(world, idempotency_key="box-2", text="Or Friday?"))["draft_id"]
    for did in (kept, pushed):
        assert app.post("/api/draft", {"delete": True, "draft_id": did}).json["ok"]
    assert waiting(app, world) is None
    app.work("drafts")
    assert drafts_on_the_server(world) == {} and waiting(app, world) is None


def test_a_reply_whose_copy_another_app_deleted_answers_nothing_any_more(app, world):
    saved = save(app, reply(world))
    with world.server.lock:
        world.server.mailbox(w.ADA, "Drafts").messages.clear()
    found = waiting(app, world)
    assert found == {"draft_id": saved["draft_id"], "in_drafts": None, "more": 0}
    r = reopen(app, found)
    assert r.status == 404 and r.json["detail"] == "That draft is no longer in Drafts."
    assert waiting(app, world) is None
