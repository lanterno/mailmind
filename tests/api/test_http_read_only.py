"""A read-only process refuses, up front, every change a person asks for that would reach a mail server: the read-only
409, before anything is queued or hidden here, so nothing waits for the next run that writes. What changes only this Mac
(a draft kept here, a rule) still works. Each test runs read-only from `read_only(app)` on, and checks what the fake
servers saw and what the database kept.
"""
from datetime import datetime, timezone

import problems
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
