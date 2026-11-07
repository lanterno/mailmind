"""A blind copy goes in the envelope and nowhere else. Whichever way a message leaves, the person Bcc'd is in RCPT TO, and in
no byte the other recipients get, in no copy a server keeps and in no answer for someone else's eyes. test_hardening.py
has what happens if a header ever gets in (the build refuses), and test_http_send.py the bytes in full, as golden files.
"""
import base64
import re
from datetime import datetime, timedelta, timezone

import pytest

from fixtures import world as w

HIDDEN = "hidden.person@example.test"
HIDDEN_NAME = "Quiet Ferris"
BCC = [f"{HIDDEN_NAME} <{HIDDEN}>"]
LUNCH = "ada-inbox-02-lunch-confirm.eml"
THREAD_END = "cy-inbox-02-thread-end.eml"
RECEIPT = "ada-inbox-05-receipt.eml"


def no_trace(raw, what="the message"):
    """`raw` names the blind copy in no way: no header, no address, no name."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    assert not re.search(r"^bcc\s*:", raw, re.I | re.M), what
    assert HIDDEN.lower() not in raw.lower(), what
    assert "hidden.person" not in raw.lower(), what
    assert HIDDEN_NAME.lower() not in raw.lower(), what


def rows(sql, *args):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def deliver(app):
    """Past the Undo Send window, and one pass of the outbox."""
    from mailmind.bootstrap.compose import outbox
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()


def new(account=w.ADA, **more):
    return {"account": account, "mode": "new", "to": ["Lin Mori <lin@example.test>"], "subject": "Fabric order",
            "text": "Hi Lin", "bcc": BCC, "idempotency_key": "k-bcc", **more}


def sent_copies(world, subject=b"Fabric order", account=w.ADA, folder="Sent"):
    """The copies of what this test sent, which a world's Sent folder already holds others beside."""
    return [m.raw for m in world.server.messages(account, folder).values() if subject in m.raw]


def only_delivery(world):
    [delivery] = world.smtp.messages
    return delivery


def holds_the_blind_copy_only_in_its_envelope(world, *also):
    d = only_delivery(world)
    assert HIDDEN in d.rcpts and set(also) <= set(d.rcpts)
    no_trace(d.data, "the bytes on the wire")
    return d


def test_a_new_message(app, world):
    assert app.post("/api/send", new(cc=["noor@studio.example.test"])).json["ok"]
    deliver(app)
    holds_the_blind_copy_only_in_its_envelope(world, "lin@example.test", "noor@studio.example.test")
    [copy] = sent_copies(world)
    no_trace(copy, "the Sent copy")


def test_a_message_with_only_a_blind_copy_says_so_to_no_one(app, world):
    assert app.post("/api/send", new(to=[])).json["ok"]
    deliver(app)
    d = only_delivery(world)
    assert d.rcpts == [HIDDEN]
    no_trace(d.data)


def test_with_a_file(app, world):
    pdf = base64.b64encode(b"%PDF-1.4\n% an order form\n").decode()
    app.post("/api/send", new(attachments=[{"filename": "order.pdf", "content_type": "application/pdf", "b64": pdf}]))
    deliver(app)
    holds_the_blind_copy_only_in_its_envelope(world, "lin@example.test")


def test_a_reply_a_reply_all_and_a_forward(app, world):
    pk = world.pk(LUNCH)
    for n, more in enumerate(({"mode": "reply", "in_reply_to_pk": pk, "subject": "Re: Lunch"},
                              {"mode": "replyall", "in_reply_to_pk": pk, "subject": "Re: Lunch", "cc": ["noor@studio.example.test"]},
                              {"mode": "forward", "in_reply_to_pk": pk, "subject": "Fwd: Lunch"})):
        assert app.post("/api/send", new(idempotency_key=f"k-{n}", **more)).json["ok"]
    deliver(app)
    assert len(world.smtp.messages) == 3
    for d in world.smtp.messages:
        assert HIDDEN in d.rcpts
        no_trace(d.data)
    copies = sent_copies(world, b"Lunch")
    assert len(copies) >= 3
    for copy in copies:
        no_trace(copy, "a Sent copy")


def test_from_gmail_which_files_what_it_sends_itself(app, world):
    """Gmail keeps what reached it, so nothing of the envelope may be in those bytes."""
    end = world.row(THREAD_END)
    app.post("/api/send", new(account=w.CY, mode="reply", in_reply_to_pk=end["pk"], subject="Re: Guild meetup venue"))
    deliver(app)
    holds_the_blind_copy_only_in_its_envelope(world)
    [copy] = sent_copies(world, b"\r\nHi Lin", w.CY, "[Gmail]/Sent Mail")
    no_trace(copy, "Gmail's own copy")


def test_a_message_scheduled_for_later(app, world):
    from mailmind.bootstrap.compose import outbox
    send_after = int(datetime.now(timezone.utc).timestamp()) + 3600
    assert app.post("/api/send", new(send_after=send_after)).json["ok"]
    [row] = rows("SELECT raw FROM outbox")
    no_trace(row["raw"], "the bytes the outbox holds")
    app.clock.shift(timedelta(hours=1, seconds=1))
    outbox.drain()
    holds_the_blind_copy_only_in_its_envelope(world)


def test_what_the_outbox_keeps_to_send_holds_it_only_as_the_envelope(app, world):
    app.post("/api/send", new())
    [row] = rows("SELECT raw, envelope FROM outbox")
    no_trace(row["raw"], "the outbox's bytes")
    assert HIDDEN in row["envelope"]


def test_a_saved_draft_keeps_it_off_the_server(app, world):
    """It's kept beside the row instead, to come back when the draft opens again. Other apps read the server's copy."""
    from mailmind.bootstrap.compose import drafts
    answer = app.post("/api/draft", new()).json
    assert answer["ok"]
    drafts.push_round()
    copies = [m.raw for m in world.server.messages(w.ADA, "Drafts").values() if b"Fabric order" in m.raw]
    assert copies, "the draft never reached the server"
    for copy in copies:
        no_trace(copy, "the draft on the server")


def test_a_person_both_to_and_bcc_is_written_for_the_to(app, world):
    """They were asked for by name, so that's no leak."""
    app.post("/api/send", new(to=[HIDDEN, "lin@example.test"]))
    deliver(app)
    d = only_delivery(world)
    assert HIDDEN in d.rcpts and HIDDEN.encode() in d.data


@pytest.mark.xfail(strict=True, reason="build_message dedupes within To, Cc and Bcc but not across them: two RCPT TOs")
@pytest.mark.parametrize("field", ["to", "cc"])
def test_a_person_named_twice_is_one_recipient_of_the_envelope(field):
    from mailmind.compose.api import build_message
    built = build_message({"account": w.ADA, "to": ["lin@example.test"], "subject": "Hi", "text": "Hello",
                           **({field: [HIDDEN, "lin@example.test"] if field == "to" else [HIDDEN]}), "bcc": [HIDDEN]})
    assert built["envelope"].count(HIDDEN) == 1


def test_replying_to_the_sent_copy_finds_no_one_hidden(app, world):
    """Reply all on your own sent message offers who it was to. It can only know that from the bytes."""
    app.post("/api/send", new())
    deliver(app)
    [uid] = [u for u, m in world.server.messages(w.ADA, "Sent").items() if b"Fabric order" in m.raw]
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    ingest.sync_folder(accounts.get(w.ADA), "Sent")
    [row] = rows("SELECT pk, recipients FROM messages WHERE account=? AND folder='Sent' AND uid=?", w.ADA, uid)
    assert HIDDEN not in (row["recipients"] or "")
    assert rows("SELECT addr FROM message_recipients WHERE addr=?", HIDDEN) == []
