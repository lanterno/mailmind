"""A read-only process refuses, up front, every change a person asks for that would reach a mail server: the read-only
409, before anything is queued or hidden here, so nothing waits for the next run that writes. What changes only this Mac
(a draft kept here, a rule) still works. Each test runs read-only from `read_only(app)` on, and checks what the fake
servers saw and what the database kept.
"""
from datetime import datetime, timezone

import problems
from fakes import anthropic_api as fa
from fixtures import world as w

REFUSED = problems.read_only()
RECEIPT = "ada-inbox-05-receipt.eml"
SCAM = "ada-inbox-12-scam.eml"
NEWSLETTER = "ada-inbox-03-newsletter.eml"
CI = "ada-inbox-04-ci.eml"
LUNCH = "ada-inbox-02-lunch-confirm.eml"
INVITE = "ada-inbox-06-outlook-invite.eml"
LOOM = "news@loom.example.test"
AT_SIX = int(datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc).timestamp())


def read_only(app):
    app.env["MAILMIND_PROFILE"] = "read-only"
    app.restart()


def rows(sql, *args):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def inbox(app):
    """Ada's Inbox as the page lists it: each copy, and whether it is read and starred."""
    items = app.get(f"/api/messages?account={w.ADA}&folder=INBOX&limit=200").json["items"]
    return sorted((m["key"], m["uid"], m["seen"], m["flagged"]) for m in items)


def new(**kw):
    return {"account": w.ADA, "mode": "new", "to": ["Lin Mori <lin@example.test>"], "subject": "Fabric order",
            "text": "Hi Lin,\n\nThe linen came in. Want half?\n\nAda", **kw}


def test_each_change_to_mail_is_refused_and_none_waits(app, world):
    read_only(app)
    shown = inbox(app)
    since = world.mark()
    for path, name, extra in (("/api/archive", RECEIPT, {}), ("/api/trash", SCAM, {}), ("/api/junk", NEWSLETTER, {}),
                              ("/api/move", CI, {"to": "Archive"}), ("/api/thread/archive", LUNCH, {}),
                              ("/api/thread/trash", LUNCH, {}), ("/api/seen", RECEIPT, {"seen": True}),
                              ("/api/flag", RECEIPT, {"flag": "flagged", "on": True}),
                              ("/api/snooze", RECEIPT, {"until": AT_SIX}), ("/api/mute", LUNCH, {})):
        r = app.post(path, {**world.copy(name), **extra})
        assert (r.status, r.json) == (409, REFUSED), path
    assert rows("SELECT id FROM mail_commands") == []
    assert rows("SELECT message_key FROM snoozes") == [] and rows("SELECT thread_id FROM muted") == []
    assert app.get("/api/pending").json["waiting"] == 0
    assert inbox(app) == shown
    assert app.post("/api/undo", {}).status == 404                     # nothing was done, so there is nothing to undo
    assert world.writes(since) == []


def test_a_send_is_refused_and_the_outbox_keeps_none(app, world):
    read_only(app)
    lunch = world.key(LUNCH)
    for label, body in (("a new message", new()), ("one sent later", new(send_after=AT_SIX)),
                        ("a reply", {**new(subject="Re: Lunch on Thursday?"), "mode": "reply", "original_key": lunch}),
                        ("an invite's answer", None)):
        if body is None:
            r = app.post("/api/invite/rsvp", {**world.copy(INVITE), "decision": "accept"})
        else:
            r = app.post("/api/send", body)
        assert (r.status, r.json) == (409, REFUSED), label
    assert rows("SELECT id FROM outbox") == []
    assert app.get("/api/outbox").json["items"] == []
    app.work("outbox")
    assert world.smtp.messages == []


def test_a_send_a_full_run_queued_isnt_asked_again_but_can_still_be_cancelled(app, world):
    oid = app.post("/api/send", new(idempotency_key="k-held")).json["outbox_id"]      # in its Undo Send window
    read_only(app)
    for path in ("/api/outbox/retry", "/api/outbox/sendnow"):
        r = app.post(path, {"id": oid})
        assert (r.status, r.json) == (409, REFUSED), path
    assert rows("SELECT state FROM outbox WHERE id=?", oid) == [{"state": "queued"}]
    assert app.post("/api/outbox/cancel", {"id": oid}).json == {"ok": True}           # it only changes this Mac
    app.work("outbox")
    assert world.smtp.messages == []


def test_a_draft_is_kept_here_but_one_on_the_server_isnt_discarded(app, world):
    pushed = app.post("/api/draft", new(subject="Fabric order (draft)")).json
    assert pushed["pushed"] is True
    read_only(app)
    since = world.mark()
    r = app.post("/api/draft", {"delete": True, "draft_id": pushed["draft_id"]})
    assert (r.status, r.json) == (409, REFUSED)
    assert rows("SELECT id FROM drafts WHERE id=?", pushed["draft_id"]) == [{"id": pushed["draft_id"]}]
    assert world.server.find(w.ADA, "Drafts", pushed["message_id"])
    kept = app.post("/api/draft", new(subject="Second thoughts")).json             # saved here: no copy to take off
    assert (kept["ok"], kept["pushed"]) == (True, False)
    assert app.post("/api/draft", {"delete": True, "draft_id": kept["draft_id"]}).json == {"ok": True, "deleted": True}
    assert world.writes(since) == []


def test_a_cleanup_is_refused_but_a_rule_is_kept(app, world):
    read_only(app)
    shown = inbox(app)
    since = world.mark()
    for path, body in (("/api/cleanup/execute", {"from_addr": LOOM}), ("/api/cleanup/batch", {"senders": [LOOM]})):
        r = app.post(path, body)
        assert (r.status, r.json) == (409, REFUSED), path
    assert LOOM not in str(app.get("/api/rules").json["rules"])
    assert inbox(app) == shown
    r = app.post("/api/rules", {"token": LOOM, "verdict": "block", **world.copy(NEWSLETTER)})
    assert (r.status, r.json["ok"], r.json["trashed"]) == (200, True, False)          # the rule, and nothing trashed
    assert LOOM in str(app.get("/api/rules").json["rules"])
    assert rows("SELECT id FROM mail_commands") == []
    assert world.writes(since) == []


def test_a_plan_carried_out_is_refused_and_files_nothing(app, world, ai):
    read_only(app)
    ai.queue(fa.answer({"intent": "act", "filter": "from:lin", "verb": "archive"}))
    plan = app.post("/api/ai/do-plan", {"cmd": "archive what Lin sent"}).json          # asking AI only reads
    assert plan["intent"] == "act" and plan["count"] > 0, plan
    shown = inbox(app)
    since = world.mark()
    r = app.post("/api/ai/do-confirm", {"plan": plan["plan"]})
    assert (r.status, r.json) == (409, REFUSED)
    assert inbox(app) == shown and rows("SELECT id FROM mail_commands") == []
    assert world.writes(since) == []


def full_run(app):
    app.env.pop("MAILMIND_PROFILE", None)
    app.restart()


def test_an_undo_that_would_reach_a_server_is_refused_and_waits_for_a_full_run(app, world):
    """What a full run filed is on its server, and so is what a mute marked read: taking either back would ask the
    server, so read-only refuses it, changes nothing here or there, and keeps it to undo on a full run."""
    lunch = world.row(LUNCH)
    world.deliver(w.ADA, "INBOX", w.variant(LUNCH, Message_ID="<mute-reply@example.test>",
                                            In_Reply_To=lunch["message_id"]))           # unread: the mute marks it read
    assert app.post("/api/mute", world.copy(LUNCH)).json["ok"]
    assert app.post("/api/archive", world.copy(RECEIPT)).status == 200
    app.work("commands")
    read_only(app)
    shown, where = inbox(app), world.where(RECEIPT)["server"]
    since = world.mark()
    for _ in range(2):                                             # the archive, still on top of the stack
        r = app.post("/api/undo", {})
        assert (r.status, r.json) == (409, REFUSED)
    assert inbox(app) == shown and world.where(RECEIPT)["server"] == where
    assert rows("SELECT id FROM mail_commands WHERE applied=0") == []
    assert world.writes(since) == []
    full_run(app)
    assert app.post("/api/undo", {}).json["ok"]                        # the archive goes back on a full run
    read_only(app)
    r = app.post("/api/undo", {})
    assert (r.status, r.json) == (409, REFUSED)
    assert rows("SELECT thread_id FROM muted") == [{"thread_id": lunch["thread_id"]}]
    assert rows("SELECT id FROM mail_commands WHERE applied=0") == []


def test_a_move_still_waiting_for_its_server_is_taken_back_here(app, world):
    world.queued_move(RECEIPT, "Archive")
    read_only(app)
    assert world.key(RECEIPT) not in [k for k, *_ in inbox(app)]           # on its way out
    since = world.mark()
    assert app.post("/api/undo", {}).json["ok"]
    assert world.key(RECEIPT) in [k for k, *_ in inbox(app)]
    assert app.get("/api/pending").json["waiting"] == 0
    assert world.writes(since) == []


def test_a_filter_asks_nothing_of_new_mail_now_or_on_a_full_run(app, world):
    """What only a sync does on its own isn't kept for a full run either: a filter would star new mail on its server,
    so read-only it asks nothing, and a full run after finds nothing waiting."""
    assert app.post("/api/filters", {"verb": "create", "q": "from:lin@example.test", "star": True}).status == 200
    read_only(app)
    since = world.mark()
    uid = world.deliver(w.ADA, "INBOX", w.variant(LUNCH, Message_ID="<filter-ro@example.test>"))
    assert rows("SELECT flagged FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.ADA, uid) == [{"flagged": 0}]
    assert rows("SELECT id FROM mail_commands") == []
    full_run(app)
    app.work("commands")
    assert world.writes(since) == []


def test_new_mail_in_a_muted_thread_is_marked_read_here_alone(app, world):
    """A thread a full run muted stays silent: what comes into it is marked read here, and its server hears nothing."""
    lunch = world.row(LUNCH)
    assert app.post("/api/mute", world.copy(LUNCH)).json["ok"]
    app.work("commands")
    read_only(app)
    since = world.mark()
    uid = world.deliver(w.ADA, "INBOX", w.variant(LUNCH, Message_ID="<muted-ro@example.test>",
                                                  In_Reply_To=lunch["message_id"]))
    assert rows("SELECT seen FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.ADA, uid) == [{"seen": 1}]
    assert rows("SELECT id FROM mail_commands WHERE applied=0") == []
    assert world.writes(since) == []


def test_snoozed_mail_wakes_unread_here_alone(app, world):
    """A snooze a full run made wakes as ever, unread, which marks it unread here alone: nothing waits for a full run."""
    receipt = world.pk(RECEIPT)
    assert app.post("/api/seen", {**world.copy(RECEIPT), "seen": True}).status == 200
    assert app.post("/api/snooze", {**world.copy(RECEIPT), "until": AT_SIX}).json["ok"]
    app.work("commands")
    read_only(app)
    app.clock.move_to(datetime(2026, 11, 2, 18, 0, 1, tzinfo=timezone.utc))
    since = world.mark()
    app.work("snoozes")
    assert rows("SELECT seen FROM messages WHERE pk=?", receipt) == [{"seen": 0}]
    assert rows("SELECT message_key FROM snoozes") == []
    assert rows("SELECT id FROM mail_commands WHERE applied=0") == []
    assert world.writes(since) == []
