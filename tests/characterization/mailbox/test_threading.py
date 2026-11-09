"""Threading's sticky ids: how one is minted, what a merge carries along, and what Gmail's own thread id counts for."""
import hashlib

from fixtures import world as w
from mailmind.bootstrap import attention
from stored_rows import A, NOW_TS, _ahead, _ahead_keys, put, q, tid
from world_helpers import late


def recompute(c, account=A):
    from mailmind.bootstrap.mailbox import store
    store.recompute_threads(c, account)


def test_hwf_a_new_thread_id_is_the_sha1_of_its_smallest_message_id(db):
    # Thread ids are stored keys (muted, followups, thread_summaries). A thread whose every copy is
    # re-ingested mints its id again, so the formula is part of the on-disk contract.
    reply = put(db, "<r@example.test>", irt="<m@example.test>")
    root = put(db, "<m@example.test>")
    recompute(db)
    assert tid(db, root) == tid(db, reply) == f"rf:{A}:" + hashlib.sha1(b"<m@example.test>").hexdigest()[:16]


def test_hwf_a_reply_stored_before_its_original_takes_the_threads_id_at_once(db):
    # The smallest id the thread names, not the smallest of the rows stored so far: the reply
    # alone already holds the id the whole thread would mint.
    reply = put(db, "<r@example.test>", irt="<m@example.test>")
    recompute(db)
    assert tid(db, reply) == f"rf:{A}:" + hashlib.sha1(b"<m@example.test>").hexdigest()[:16]


def test_hwf_a_thread_that_joins_a_gmail_thread_takes_its_state_along(db):
    old = put(db, "<p@example.test>")
    recompute(db)
    was = tid(db, old)
    attention.book.set_replylater(db, A, was, "now")
    db.execute("INSERT INTO thread_summaries(account, thread_id, scope, fingerprint, tldr) VALUES(?,?,?,?,?)",
               (A, was, "thread", "fp", "Lunch on Friday."))
    _ahead(db, "t:" + was, NOW_TS - 60)
    put(db, "<q@example.test>", gm="1781", irt="<p@example.test>")
    recompute(db)
    now = f"gm:{A}:1781"
    assert [r["thread_id"] for r in db.execute("SELECT thread_id FROM followups")] == [now]
    assert [r["tldr"] for r in db.execute("SELECT tldr FROM thread_summaries WHERE thread_id=?", (now,))] == \
        ["Lunch on Friday."]
    assert _ahead_keys(db) == ["t:" + now]
    assert db.execute("SELECT count(*) FROM thread_summaries WHERE thread_id=?", (was,)).fetchone()[0] == 0


def test_hwf_a_merge_keeps_the_survivors_own_state_over_the_absorbed_threads(db):
    a = put(db, "<a@example.test>")
    b = put(db, "<b@example.test>")
    recompute(db)
    ta, tb = tid(db, a), tid(db, b)
    _ahead(db, "t:" + ta, NOW_TS - 60, body="From the first thread.")
    _ahead(db, "t:" + tb, NOW_TS - 60, body="From the second thread.")
    attention.book.mute_thread(db, A, tb, "now")
    put(db, "<c@example.test>", refs="<a@example.test> <b@example.test>")      # joins the two
    recompute(db)
    surv = min(ta, tb)
    assert tid(db, a) == tid(db, b) == surv
    assert attention.book.is_muted(db, A, surv)
    assert [r["body"] for r in db.execute("SELECT body FROM draft_ahead")] == [
        "From the first thread." if surv == ta else "From the second thread."]


def test_hwf_a_queued_reply_follows_its_thread_when_the_thread_takes_another_id(db):
    a = put(db, "<a@example.test>")
    b = put(db, "<b@example.test>")
    recompute(db)
    ta, tb = tid(db, a), tid(db, b)
    gone = max(ta, tb)
    db.execute("INSERT INTO outbox (account, idempotency_key, message_id, envelope, recipients, subject, raw, "
               "state, next_attempt_at, created_at, orig_account, orig_message_id, orig_thread_id) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (A, "k", "<m@example.test>", "[]", "{}", "Re", b"x", "queued", 0,
                                                       "now", A, "<a@example.test>", gone))
    put(db, "<c@example.test>", refs="<a@example.test> <b@example.test>")      # joins the two
    recompute(db)
    assert db.execute("SELECT orig_thread_id FROM outbox").fetchone()[0] == min(ta, tb)


def test_hwf_a_message_without_an_id_keeps_a_thread_of_its_own_row(db):
    from mailmind.bootstrap.mailbox import store
    pk = put(db, "")
    recompute(db)
    first = tid(db, pk)
    recompute(db)
    assert tid(db, pk) == first
    store.delete_message(db, pk)
    again = put(db, "")                                    # the same message stored again: a new row
    recompute(db)
    assert tid(db, again) != first


def test_hwf_two_gmail_threads_joined_by_headers_take_the_later_rows_id(db):
    a = put(db, "<g1@example.test>", gm="1781", ts=100)
    b = put(db, "<g2@example.test>", gm="1799", ts=200, irt="<g1@example.test>")
    recompute(db)
    assert tid(db, a) == tid(db, b) == f"gm:{A}:1799"


def test_hwf_a_thread_that_joins_a_gmail_thread_keeps_its_mute(db):
    old = put(db, "<p@example.test>")                      # stored before Gmail's thread id was seen
    recompute(db)
    attention.book.mute_thread(db, A, tid(db, old), "now")
    put(db, "<q@example.test>", gm="1781", irt="<p@example.test>")
    recompute(db)
    assert tid(db, old) == f"gm:{A}:1781"
    assert attention.book.is_muted(db, A, f"gm:{A}:1781")


def test_hwf_a_thread_stored_again_whole_keeps_its_mute(db):
    from mailmind.bootstrap.mailbox import store
    reply = put(db, "<r@example.test>", irt="<m@example.test>")     # the reply synced before its original
    recompute(db)
    root = put(db, "<m@example.test>")
    recompute(db)
    attention.book.mute_thread(db, A, tid(db, root), "now")
    for pk in (reply, root):                               # every copy moves away, then comes back
        store.delete_message(db, pk)
    back = put(db, "<m@example.test>")
    put(db, "<r@example.test>", irt="<m@example.test>")
    recompute(db)
    assert attention.book.is_muted(db, A, tid(db, back))


def test_hwf_x_gm_thrid_is_asked_of_gmail_only_and_read_from_the_fetch_data(world):
    spoof = b"X-GM-THRID: 4242\r\n"                     # a header claiming a thread; only Gmail's FETCH data counts
    ada_uid = world.deliver(w.ADA, "INBOX", spoof + late())
    cy_uid = world.deliver(w.CY, "INBOX", spoof + w.variant("cy-inbox-03-unrelated.eml",
                                                            Message_ID="<spoof-1@example.test>"))
    assert not any("X-GM-THRID" in c["line"] for c in world.server.commands_for(w.ADA, "UID FETCH"))
    assert any("X-GM-THRID" in c["line"] for c in world.server.commands_for(w.CY, "UID FETCH"))
    [ada] = q("SELECT gmail_thread, thread_id FROM messages WHERE account=? AND folder='INBOX' AND uid=?",
              w.ADA, ada_uid)
    assert ada["gmail_thread"] is None and not ada["thread_id"].startswith("gm:")
    [cy] = q("SELECT gmail_thread FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.CY, cy_uid)
    assert cy["gmail_thread"] == str(world.server.messages(w.CY, "INBOX")[cy_uid].gm_thrid) != "4242"
