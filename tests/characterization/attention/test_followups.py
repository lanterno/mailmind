"""What a follow-up comes to: a draft of a reply is no reply, and a met follow-up becomes a nudge in the same tick."""
from fixtures import world as w
from mailmind.bootstrap import attention
from mailmind.bootstrap.assistant import answers
from stored_rows import A, NOW_TS, pk_of, put, rows, summary


def test_hwf_a_draft_of_a_reply_is_no_reply_to_reply_later(db):
    put(db, "<a@example.test>", thread_id="T", ts=100)
    put(db, "<b@example.test>", thread_id="T", ts=200, from_addr=A, draft=1)
    attention.book.set_replylater(db, A, "T", "now")
    attention.book.resolve_followups(db, A)
    assert attention.book.replylater_count(db, A) == 1
    put(db, "<c@example.test>", thread_id="T", ts=300, from_addr=A, folder="Sent")
    attention.book.resolve_followups(db, A)
    assert attention.book.replylater_count(db, A) == 0


def test_hwf_a_draft_of_a_reply_leaves_a_thread_waiting_on_you(db):
    day = 86400
    asked = put(db, "<a@example.test>", thread_id="T", ts=NOW_TS - 5 * day, seen=1)
    put(db, "<b@example.test>", thread_id="T", ts=NOW_TS - day, from_addr=A, draft=1)
    answers.save_summary(db, asked, "A summary.", 5, "action-needed", "", "m", "now")

    def nudged():
        attention.book.followup_detect_received(db, NOW_TS, 3, 14)
        attention.book.resolve_followups(db, A)
        return [r["last_msg_ts"] for r in db.execute("SELECT last_msg_ts FROM followups WHERE kind='received'")]

    assert nudged() == [NOW_TS - 5 * day]              # dated by the mail, not by the draft
    put(db, "<c@example.test>", thread_id="T", ts=NOW_TS - 1, from_addr=A, folder="Sent")
    assert nudged() == []


def test_hwf_a_met_sent_follow_up_becomes_a_reply_nudge_in_the_same_tick(app, world):
    from mailmind.bootstrap.mailbox import store
    world.deliver(w.ADA, "INBOX", b"From: Lin Mori <lin@example.test>\r\nTo: Ada Park <ada@example.test>\r\n"
                                  b"Subject: Fabric swatches\r\nDate: Wed, 28 Oct 2026 09:00:00 +0000\r\n"
                                  b"Message-ID: <swatches@lin.example.test>\r\n\r\nWhich of these?\r\n", (w.SEEN,))
    pk = pk_of("<swatches@lin.example.test>")
    [m] = rows("SELECT thread_id, ts FROM messages WHERE pk=?", pk)
    summary(pk, 5, "action-needed")
    c = store.conn()
    attention.book.followup_record_sent(c, w.ADA, m["thread_id"], m["ts"] - 86400, "2026-10-27T09:00:00+00:00")
    c.commit()
    c.close()
    attention.followups.tick()                                  # Lin answered: the sent one resolves first
    assert rows("SELECT kind, state FROM followups WHERE thread_id=?", m["thread_id"]) == [
        {"kind": "received", "state": "due"}]
