"""Sending, end to end: /api/send and its siblings queue a message in the outbox, and the outbox
delivers it through the fake SMTP server, files the Sent copy, flags the original \\Answered, and
cleans up the draft it came from. Each step pins the answer, the outbox row, the bytes on the wire
and what changed on the IMAP server. The golden files are in tests/golden/http_send/.

No outbox worker runs on its own: a test lets the clock pass the Undo Send window and has the server
run one outbox pass (app.work), so every step happens in a known order. Message-IDs, MIME boundaries and
idempotency keys the server draws at random are named in the record.

Whether the outbox publishes Sent or Failed on the in-process bus is pinned in
tests/characterization/test_http_send_in_process.py: no answer over HTTP tells the two apart.
"""
import base64
import email
import email.policy
import re
from datetime import datetime, timedelta, timezone

import pytest

from fixtures import world as w
from mailmind.compose.domain import outbox_item

OUTBOX_COLS = ("id", "account", "state", "attempts", "last_error", "delivered", "sent_appended",
               "answered_set", "followup_recorded", "draft_deleted", "subject", "envelope", "recipients",
               "next_attempt_at")


def rows(sql, *args):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


class Sender:
    """A test's view of the send path, naming random values in the golden record as it goes."""

    def __init__(self, app, golden, world):
        self.app, self.golden, self.world = app, golden, world
        self.mark = world.mark()
        self.wire = 0
        self.named = set()
        self.window = None

    def post(self, label, path, body):
        r = self.golden.record(label, self.app.post(path, body))
        if isinstance(r.json, dict) and r.json.get("message_id"):      # a saved draft's
            self.name("msg", r.json["message_id"])
        if isinstance(r.json, dict) and "undo_seconds" in r.json:
            # The Undo Send window is the server's to say, in each send's answer, as the page reads it.
            self.window = r.json["undo_seconds"]
        return r

    def name(self, kind, value):
        if value and value not in self.named:
            self.named.add(value)
            self.golden.alias(value, f"<{kind} {len(self.named)}>")

    def name_message(self, raw):
        msg = email.message_from_bytes(raw, policy=email.policy.compat32)
        self.name("msg", (msg.get("Message-ID") or "").strip())
        for part in msg.walk():
            self.name("boundary", part.get_boundary())

    def outbox(self, label):
        out = rows(f"SELECT {', '.join(OUTBOX_COLS)}, message_id, raw FROM outbox ORDER BY id")
        for r in out:
            self.name_message(r.pop("raw"))
        self.golden.note(label, out)

    def drain(self, label, seconds=None):
        """Let the Undo Send window pass (or `seconds`), have the server run one outbox pass, and note
        what went over the wire and what changed on the IMAP server."""
        self.app.clock.shift(timedelta(seconds=self.window + 1 if seconds is None else seconds))
        self.app.work("outbox")
        sent = self.world.smtp.messages[self.wire:]
        self.wire = len(self.world.smtp.messages)
        for d in sent:
            self.name_message(d.data)
        self.golden.note(f"{label}: on the wire", [
            {"login": d.user, "mail_from": d.mail_from, "rcpts": d.rcpts,
             "data": d.data.decode("utf-8", "replace")} for d in sent])
        self.golden.note(f"{label}: server writes", self.world.writes(self.mark))
        self.mark = self.world.mark()
        self.outbox(f"{label}: the outbox")


@pytest.fixture
def send(app, golden, world):
    return Sender(app, golden, world)


def new(account=w.ADA, **kw):
    return {"account": account, "mode": "new", "to": ["Lin Mori <lin@example.test>"], "subject": "Fabric order",
            "text": "Hi Lin,\n\nThe linen came in. Want half?\n\nAda", **kw}


# ── a new message, a reply, a reply-all, a forward ─────────────────────────────

def test_a_new_message_with_cc_bcc_and_a_file(send):
    pdf = base64.b64encode(b"%PDF-1.4\n% a fictional order form\n").decode()
    send.post("send", "/api/send", new(cc=["noor@studio.example.test"], bcc=["ada.archive@example.test"],
                                       attachments=[{"filename": "order.pdf", "content_type": "application/pdf",
                                                     "b64": pdf}], idempotency_key="k-new"))
    send.outbox("queued")
    send.drain("before the Undo Send window ends", seconds=5)
    send.drain("after it")
    send.golden.note("Ada's Sent on the server", sorted(
        (m.headers()["Subject"], sorted(m.flags)) for m in send.world.server.messages(w.ADA, "Sent").values()))


def test_a_reply_threads_quotes_and_marks_the_original(send):
    lunch = send.world.key("ada-inbox-02-lunch-confirm.eml")
    send.post("reply to Lin", "/api/send", {"account": w.ADA, "mode": "reply", "original_key": lunch,
                                            "to": ["lin@example.test"], "subject": "Re: Lunch on Thursday?",
                                            "text": "Great, see you then.", "idempotency_key": "k-reply"})
    send.drain("sent")
    send.golden.note("the original", send.world.where("ada-inbox-02-lunch-confirm.eml"))
    send.golden.note("follow-ups", rows("SELECT account, kind, state FROM followups"))


def test_reply_all_from_gmail_files_no_sent_copy_of_its_own(send):
    end = send.world.key("cy-inbox-02-thread-end.eml")
    send.post("reply all to Mae", "/api/send", {"account": w.CY, "mode": "replyall", "original_key": end,
                                                "to": ["mae@guild.example.test"], "cc": ["guild@example.test"],
                                                "subject": "Re: Guild meetup venue", "text": "Perfect, thanks!",
                                                "idempotency_key": "k-replyall"})
    send.drain("sent")
    send.golden.note("Cy's Sent Mail on the server", [
        (str(m.headers()["Subject"]), m.gm_thrid == send.world.server.messages(w.CY, "INBOX")[
            send.world.uid("cy-inbox-02-thread-end.eml")].gm_thrid)
        for m in send.world.server.messages(w.CY, "[Gmail]/Sent Mail").values()])


def test_a_forward_from_proton_carries_the_attachments(send):
    send.post("forward Bea's draft with its PDF", "/api/send", {
        "account": w.BEA, "mode": "forward", "original_key": send.world.key("bea-drafts-01-apple-mail.eml"),
        "to": ["ivo@harbor.example.test"], "subject": "Fwd: Re: Contract draft v2", "text": "As promised.",
        "idempotency_key": "k-forward"})
    send.drain("sent")
    send.post("forward without the attachments", "/api/send", {
        "account": w.BEA, "mode": "forward", "original_key": send.world.key("bea-drafts-01-apple-mail.eml"),
        "forward_attachments": False, "to": ["ivo@harbor.example.test"], "subject": "Fwd: notes",
        "text": "Text only.", "idempotency_key": "k-forward-2"})
    send.drain("sent too")


# ── Undo Send, send later, send now, double submit ───────────────────────────

def test_undo_send_cancels_before_the_window_ends(send):
    oid = send.post("send", "/api/send", new(idempotency_key="k-undo")).json["outbox_id"]
    send.post("cancel it", "/api/outbox/cancel", {"id": oid})
    send.drain("after the window")
    send.post("cancel it again", "/api/outbox/cancel", {"id": oid})
    send.golden.record("the outbox list", send.app.get("/api/outbox"))


def test_send_later_waits_for_its_time(send):
    at = int(datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc).timestamp())
    send.post("send at 18:00", "/api/send", new(send_after=at, idempotency_key="k-later"))
    send.drain("at 12:00:11")
    send.app.clock.move_to(datetime(2026, 11, 2, 18, 0, 1, tzinfo=timezone.utc))
    send.drain("at 18:00:01", seconds=0)
    for label, when in (("a time that has passed", int(datetime(2026, 11, 2, 11, 0, tzinfo=timezone.utc).timestamp())),
                        ("a time over a year away", int(datetime(2027, 12, 1, tzinfo=timezone.utc).timestamp())),
                        ("a time that isn't one", "soon")):
        send.post(f"send at {label}", "/api/send", new(send_after=when))


def test_send_now_skips_the_window_and_a_resubmit_dedupes(send):
    oid = send.post("send", "/api/send", new(idempotency_key="k-now")).json["outbox_id"]
    send.post("the same request again", "/api/send", new(idempotency_key="k-now"))
    send.post("send now", "/api/outbox/sendnow", {"id": oid})
    send.drain("without waiting", seconds=0)
    send.post("send now, once sent", "/api/outbox/sendnow", {"id": oid})
    send.post("send now, no such id", "/api/outbox/sendnow", {"id": 999})


# ── when SMTP fails ──────────────────────────────────────────────────────

def test_a_temporary_failure_backs_off_then_gives_up_then_retries(send):
    send.world.smtp.fail("RCPT", "451 4.3.0 try again later", times=None)
    send.post("send", "/api/send", new(idempotency_key="k-fail"))
    send.drain("first attempt")
    for attempt in range(2, outbox_item.MAX_ATTEMPTS + 1):
        send.drain(f"attempt {attempt}", seconds=outbox_item.BACKOFF_CAP)
    send.world.smtp.clear_faults()
    send.drain("after giving up, nothing more", seconds=outbox_item.BACKOFF_CAP)
    send.post("retry", "/api/outbox/retry", {"id": 1})
    send.drain("after retry", seconds=0)
    send.post("retry a sent one", "/api/outbox/retry", {"id": 1})


def test_a_refused_login_and_a_rejected_message_give_up_at_once(send):
    send.world.smtp.users[w.ADA] = "changed"
    send.post("send with a password the server no longer takes", "/api/send", new(idempotency_key="k-auth"))
    send.drain("login refused")
    send.drain("not tried again", seconds=outbox_item.BACKOFF_CAP)
    send.world.smtp.users[w.ADA] = send.world.server.accounts[w.ADA].password
    send.world.smtp.fail("DATA-END", "554 5.7.1 message rejected as spam")
    send.post("retry, the password put right", "/api/outbox/retry", {"id": 1})
    send.drain("message rejected", seconds=0)
    send.drain("not sent again", seconds=outbox_item.BACKOFF_CAP)


def test_a_recipient_refused_for_good_is_given_up_at_once(app, world):
    world.smtp.fail("RCPT", "550 5.1.1 no such user", times=None)
    sent = app.post("/api/send", new(idempotency_key="k-nobody")).json
    oid = sent["outbox_id"]
    app.clock.shift(timedelta(seconds=sent["undo_seconds"] + 1))
    app.work("outbox")
    [row] = rows("SELECT state, attempts, last_error FROM outbox")
    assert (row["state"], row["attempts"], row["last_error"]) == ("error", 1, "recipient refused")
    assert len([c for c in world.smtp.commands if c["command"] == "RCPT"]) == 1
    assert oid == 1


def test_a_connection_lost_after_data_isnt_sent_again(send):
    # The server took the message but the line dropped before it said so. The outbox can't tell,
    # so a person decides.
    send.world.smtp.drop_after_data()
    send.post("send", "/api/send", new(idempotency_key="k-drop"))
    send.drain("the drop")
    send.drain("the retry", seconds=3600)
    send.golden.note("copies delivered", len(send.world.smtp.messages))


# ── refusals ─────────────────────────────────────────────────────────────

def test_send_refusals(send):
    lunch = send.world.key("ada-inbox-02-lunch-confirm.eml")
    for label, body in (
        ("an account that isn't configured", new(account="zed@example.test")),
        ("no recipients", new(to=[])),
        ("a recipient that isn't an address", new(to=["lin at example dot test"])),
        ("a header break in the subject", new(subject="Hi\r\nBcc: everyone@example.test")),
        ("a reply with no original", {**new(), "mode": "reply"}),
        ("a reply to a missing original", {**new(), "mode": "reply", "original_key": 999999}),
        ("an attachment that isn't base64", new(attachments=[{"filename": "x.bin", "b64": "%%%"}])),
        ("attachments that aren't a list", new(attachments="x")),
        ("too many recipients", new(to=[f"p{i}@example.test" for i in range(101)])),
        ("resume data that isn't an object", {**new(), "resume": "x"}),
        ("a reply by key that is fine", {**new(), "mode": "reply", "original_key": lunch, "idempotency_key": "k-ok"}),
    ):
        send.post(f"send: {label}", "/api/send", body)
    send.outbox("queued after the refusals")
    [raw] = rows("SELECT raw FROM outbox WHERE subject LIKE 'Hi%'")
    head = raw["raw"].split(b"\r\n\r\n", 1)[0].decode()
    send.golden.note("the header-break message's Subject and Bcc lines",
                     [h for h in head.split("\r\n") if h.lower().startswith(("subject:", "bcc:", " "))])


# ── drafts ───────────────────────────────────────────────────────────────

def test_save_resave_resume_send_and_delete_drafts(send):
    first = send.post("save a draft", "/api/draft", new(subject="Fabric order (draft)")).json
    send.golden.note("server writes for the first save", send.world.writes(send.mark))
    send.mark = send.world.mark()
    did = first["draft_id"]
    send.post("save it again with more text", "/api/draft", new(subject="Fabric order (draft)", draft_id=did,
                                                               text="Hi Lin,\n\nThe linen came in."))
    send.golden.note("server writes for the resave", send.world.writes(send.mark))
    send.mark = send.world.mark()
    send.golden.note("drafts", rows("SELECT id, account, folder, uid, mode, subject, own_text, bcc FROM drafts"))
    send.golden.note("Ada's Drafts on the server", sorted(
        str(m.headers()["Subject"]) for m in send.world.server.messages(w.ADA, "Drafts").values()))
    send.app.work("sync", w.ADA, "Drafts")      # the Drafts list shows it, as a sync would
    [draft] = rows("SELECT m.pk FROM drafts d JOIN messages m ON m.account=d.account AND m.folder=d.folder "
                   "AND m.uid=d.uid WHERE d.id=?", did)
    resumed = send.post("reopen it", "/api/draft/resume", w.copy_of(draft["pk"])).json
    send.post("save an empty draft", "/api/draft", {"account": w.ADA, "mode": "new"})
    send.post("send it", "/api/send", new(subject="Fabric order (draft)", draft_id=did, text=resumed.get("text", ""),
                                          idempotency_key="k-draft"))
    send.drain("sent")
    send.golden.note("drafts after the send", rows("SELECT id FROM drafts"))
    second = send.post("save another", "/api/draft", new(subject="Second thoughts")).json
    send.post("delete it", "/api/draft", {"delete": True, "draft_id": second["draft_id"]})
    send.golden.note("server writes for the delete", send.world.writes(send.mark))
    send.golden.note("Ada's Drafts at the end", sorted(
        str(m.headers()["Subject"]) for m in send.world.server.messages(w.ADA, "Drafts").values()))


def test_reopen_a_draft_another_client_saved(send):
    send.post("reopen Thunderbird's draft", "/api/draft/resume",
              send.world.copy("ada-drafts-02-thunderbird-v2.eml"))
    send.post("reopen Apple Mail's draft", "/api/draft/resume",
              send.world.copy("bea-drafts-01-apple-mail.eml"))
    send.post("reopen a message that isn't a draft", "/api/draft/resume",
              send.world.copy("ada-inbox-02-lunch-confirm.eml"))
    send.post("reopen nothing", "/api/draft/resume", {})


# ── calendar replies ─────────────────────────────────────────────────────

def test_rsvp_to_an_invite(send):
    invite = send.world.copy("ada-inbox-06-outlook-invite.eml")
    send.post("accept the design review", "/api/invite/rsvp", {**invite, "decision": "accept"})
    send.drain("sent")
    send.post("an unknown decision", "/api/invite/rsvp", {**invite, "decision": "maybe"})
    send.post("a message with no invite", "/api/invite/rsvp",
              {**send.world.copy("ada-inbox-02-lunch-confirm.eml"), "decision": "accept"})
    send.post("no message", "/api/invite/rsvp", {"decision": "accept"})


def test_a_double_submitted_rsvp_queues_once(app, world):
    invite = world.copy("ada-inbox-06-outlook-invite.eml")
    a = app.post("/api/invite/rsvp", {**invite, "decision": "accept"}).json
    b = app.post("/api/invite/rsvp", {**invite, "decision": "accept"}).json
    assert (a["outbox_id"], b["queued"]) == (b["outbox_id"], False)


def test_a_changed_mind_about_an_invite_sends_each_answer(app, world):
    invite = world.copy("ada-inbox-06-outlook-invite.eml")
    ids = [app.post("/api/invite/rsvp", {**invite, "decision": d}).json["outbox_id"]
           for d in ("accept", "decline", "accept", "accept")]
    assert len(set(ids[:3])) == 3 and ids[3] == ids[2]


def test_wire_dates_follow_the_frozen_clock(send):
    send.post("send", "/api/send", new(idempotency_key="k-date"))
    send.drain("sent")
    [d] = send.world.smtp.messages
    assert re.search(rb"^Date: Mon, 02 Nov 2026 12:00:00 \+0000\r?$", d.data, re.M)
