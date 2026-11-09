"""A reply or a forward once its original has moved or gone: what a first save carries, and what the send still does to the original."""
import email
import email.policy
from datetime import timedelta

from fixtures import world as w
from stored_rows import q
from world_helpers import LUNCH_CONFIRM


def test_a_reply_marks_its_original_answered_after_the_original_is_archived(app, world):
    from mailmind.bootstrap.compose import outbox
    r = app.post("/api/send", {"account": w.ADA, "mode": "reply", "original_key": world.key(LUNCH_CONFIRM),
                               "to": ["lin@example.test"], "subject": "Re: Lunch on Thursday?", "text": "See you.",
                               "idempotency_key": "k-reply-archive"}).json
    assert r["ok"]
    assert app.post("/api/archive", {**world.copy(LUNCH_CONFIRM)}).json["ok"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    [(_, box, _, flags)] = world.where(LUNCH_CONFIRM)["server"]
    assert (box, "\\Answered" in flags) == ("Archive", True)


LUNCH = "ada-inbox-02-lunch-confirm.eml"


def forget_row(world, name):
    """The original's row goes, as reconcile pruning it, expiry or a queued archive would."""
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    store.delete_message(c, world.pk(name))
    c.commit()
    c.close()


def replying(world, **kw):
    return {"account": w.ADA, "mode": "reply", "original_key": world.key(LUNCH), "to": ["lin@example.test"],
            "subject": "Re: Lunch on Thursday?", "text": "See you there.", **kw}


def test_hwf_a_reply_saved_once_no_longer_needs_its_originals_row(app, world):
    from mailmind.bootstrap.compose import outbox
    key, resave = world.key(LUNCH), replying(world)
    first = app.post("/api/draft", resave).json
    assert first["resume"]["in_reply_to"] and first["resume"]["tail_text"].startswith("\n\nOn ")
    forget_row(world, LUNCH)
    assert app.post("/api/draft", replying(world) | {"original_key": key}).status == 404      # the old way
    named = {"original_key": key, "draft_id": first["draft_id"], "resume": first["resume"]}
    saved = app.post("/api/draft", replying(world) | named | {"text": "See you there. I booked."}).json
    assert saved["ok"] and "resume" not in saved
    sent = app.post("/api/send", replying(world) | named | {"text": "See you there. I booked.",
                                                            "idempotency_key": "k-gone"}).json
    assert sent["ok"], sent
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    [out] = world.smtp.messages
    assert email.message_from_bytes(out.data)["In-Reply-To"] and b"I booked." in out.data
    [(_, box, _, flags)] = world.where(LUNCH)["server"]
    assert (box, "\\Answered" in flags) == ("INBOX", True)


def test_hwf_a_forward_saved_once_carries_its_files_itself(app, world):
    from mailmind.bootstrap.compose import outbox
    with_file = w.variant(LUNCH, Message_ID="<with-file@example.test>")
    msg = email.message_from_bytes(with_file, policy=email.policy.default)
    msg.add_attachment(b"linen swatch", maintype="application", subtype="octet-stream", filename="swatch.bin")
    world.deliver(w.ADA, "INBOX", msg.as_bytes())
    [orig] = q("SELECT message_key FROM messages WHERE message_id='<with-file@example.test>'")
    body = {"account": w.ADA, "mode": "forward", "original_key": orig["message_key"], "to": ["bea@example.test"],
            "subject": "Fwd: Lunch", "text": "For you."}
    first = app.post("/api/draft", body).json
    assert [(f["filename"], f["size"]) for f in first["files"]] == [("swatch.bin", 12)]
    assert first["resume"]["in_reply_to"] == ""
    c_files = [{k: f[k] for k in ("filename", "content_type", "b64")} for f in first["files"]]
    named = {"draft_id": first["draft_id"], "resume": first["resume"], "attachments": c_files}
    assert app.post("/api/send", body | named | {"idempotency_key": "k-fwd"}).json["ok"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    [out] = world.smtp.messages
    parts = list(email.message_from_bytes(out.data, policy=email.policy.default).iter_attachments())
    assert [p.get_filename() for p in parts] == ["swatch.bin"]              # once, not twice


def test_hwf_a_reply_records_its_follow_up_after_its_original_is_archived(app, world):
    from mailmind.bootstrap.compose import outbox
    assert app.post("/api/send", replying(world, idempotency_key="k-fu")).json["ok"]
    [before] = q("SELECT thread_id FROM messages WHERE pk=?", world.pk(LUNCH))
    assert app.post("/api/archive", world.copy(LUNCH)).json["ok"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    assert [r["thread_id"] for r in q("SELECT thread_id FROM followups WHERE kind='sent'")] == [before["thread_id"]]
