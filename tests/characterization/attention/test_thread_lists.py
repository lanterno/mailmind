"""The muted, Reply Later and Waiting lists: their badges, the message each row shows, and a dismissal."""
from mailmind.bootstrap import attention
from stored_rows import A, put


def test_hwf_badges_count_only_threads_that_still_have_mail(db):
    put(db, "<a@example.test>", thread_id="T1")
    put(db, "<b@example.test>", thread_id="T2")
    put(db, "<c@example.test>", thread_id="T3")
    for t in ("T1", "GONE1"):
        attention.book.mute_thread(db, A, t, "now")
    for t in ("T2", "GONE2"):
        attention.book.set_replylater(db, A, t, "now")
    db.executemany("INSERT INTO followups(account, thread_id, kind, state, due_ts) VALUES (?,?,?,?,?)",
                   [(A, "T3", "sent", "due", 1), (A, "GONE3", "received", "due", 2)])
    assert (attention.book.muted_count(db, A), len(attention.book.list_muted(db, A))) == (1, 1)
    assert (attention.book.replylater_count(db, A), len(attention.book.list_replylater(db, A))) == (1, 1)
    assert (attention.book.waiting_count(db, A), len(attention.book.list_waiting(db, A))) == (1, 1)


def test_hwf_a_listed_thread_shows_its_latest_message_the_higher_pk_on_a_tie(db):
    put(db, "<1@example.test>", thread_id="T", ts=100)
    put(db, "<2@example.test>", thread_id="T", ts=200)
    latest = put(db, "<3@example.test>", thread_id="T", ts=200, folder="Sent")
    put(db, "<4@example.test>", thread_id="U", ts=300)
    waiting = put(db, "<5@example.test>", thread_id="U", ts=300)
    attention.book.mute_thread(db, A, "T", "now")
    attention.book.set_replylater(db, A, "T", "now")
    db.execute("INSERT INTO followups(account, thread_id, kind, state, due_ts) VALUES (?,?,?,?,?)",
               (A, "U", "sent", "due", 1))
    assert [(r["pk"], r["thread_count"]) for r in attention.book.list_muted(db, A)] == [(latest, 3)]
    assert [(r["pk"], r["thread_count"]) for r in attention.book.list_replylater(db, A)] == [(latest, 3)]
    assert [(r["pk"], r["thread_count"]) for r in attention.book.list_waiting(db, A)] == [(waiting, 2)]


def test_hwf_a_dismissed_reply_later_thread_leaves_the_stack(db):
    put(db, "<a@example.test>", thread_id="T")
    attention.book.set_replylater(db, A, "T", "now")
    attention.book.dismiss_followup(db, A, "T", "now")          # what POST /api/followups/dismiss does
    assert [r["thread_id"] for r in attention.book.list_replylater(db, A)] == []
    assert attention.book.replylater_count(db, A) == 0           # nor counted on the rail
    attention.book.set_replylater(db, A, "T", "later")     # pinned again, it's back
    assert ([r["thread_id"] for r in attention.book.list_replylater(db, A)], attention.book.replylater_count(db, A)) == (["T"], 1)
