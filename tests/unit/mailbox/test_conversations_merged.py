"""When two threads become one, each context moves what it keeps under the id that went, in the merge's own
transaction: mutes and follow-ups, thread summaries and the reply drafted ahead, a queued reply's follow-up."""
import itertools
import sqlite3

import pytest

A = "ada@example.test"
_uid = itertools.count(1)


@pytest.fixture
def db(data_dir):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    yield c
    c.close()


@pytest.fixture(params=["thread_stored", "recompute_threads"])
def regroup(request):
    """Either way a thread's id can change: what a sync does, or the repair."""
    from mailmind.bootstrap.mailbox import store

    def run(c):
        getattr(store, request.param)(c, A)
        c.commit()
    run.quiet = lambda c: getattr(store, request.param)(c, A)
    return run


def add(c, mid, *, irt="", refs=""):
    from mailmind.bootstrap.mailbox import store
    pk = store.upsert_message(c, {"account": A, "kind": "imap", "folder": "INBOX", "uid": next(_uid), "message_id": mid,
                                  "gmail_thread": None, "from_addr": "lin@example.test", "from_name": "Lin",
                                  "subject": "Hello", "date_iso": None, "ts": 0, "snippet": "",
                                  "bucket": "personal/protected", "is_junk": 0})
    c.execute("UPDATE messages SET in_reply_to=?, refs=? WHERE pk=?", (irt, refs, pk))
    return pk


def tid(c, pk):
    return c.execute("SELECT thread_id FROM messages WHERE pk=?", (pk,)).fetchone()[0]


def two_threads(c, regroup):
    """Two threads: the id that will survive when a message joins them, and the one that will go."""
    p, q = add(c, "<p@example.test>"), add(c, "<q@example.test>")
    regroup(c)
    return sorted((tid(c, p), tid(c, q)))


def keep_everything_under(c, thread):
    c.execute("INSERT INTO muted(account, thread_id, muted_at) VALUES (?,?,?)", (A, thread, "2026-11-01"))
    c.execute("INSERT INTO followups(account, thread_id, kind, state) VALUES (?,?,?,?)", (A, thread, "sent", "pending"))
    c.execute("INSERT INTO thread_summaries(account, thread_id, fingerprint, tldr) VALUES (?,?,?,?)", (A, thread, "f", "Lunch"))
    c.execute("INSERT INTO draft_ahead(account, ref_key, fp, body) VALUES (?,?,?,?)", (A, "t:" + thread, "f", "Thursday."))
    c.execute("INSERT INTO outbox(account, idempotency_key, message_id, envelope, recipients, raw, created_at, "
              "orig_account, orig_message_id, orig_thread_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
              (A, "k", "<sent@example.test>", "[]", "{}", b"x", "2026-11-01", A, "<p@example.test>", thread))
    c.commit()


def everything_kept(c):
    return {"muted": [r[0] for r in c.execute("SELECT thread_id FROM muted")],
            "followups": [r[0] for r in c.execute("SELECT thread_id FROM followups")],
            "thread_summaries": [r[0] for r in c.execute("SELECT thread_id FROM thread_summaries")],
            "draft_ahead": [r[0][2:] for r in c.execute("SELECT ref_key FROM draft_ahead")],
            "outbox": [r[0] for r in c.execute("SELECT orig_thread_id FROM outbox")]}


def test_each_context_moves_what_it_keeps(db, regroup):
    survivor, absorbed = two_threads(db, regroup)
    keep_everything_under(db, absorbed)
    add(db, "<j@example.test>", refs="<p@example.test> <q@example.test>")
    regroup(db)
    assert everything_kept(db) == dict.fromkeys(("muted", "followups", "thread_summaries", "draft_ahead", "outbox"), [survivor])


def test_a_keeper_that_fails_rolls_the_merge_back(db, regroup, monkeypatch):
    from mailmind.bootstrap.mailbox import store
    _, absorbed = two_threads(db, regroup)
    keep_everything_under(db, absorbed)
    before = everything_kept(db)
    p = db.execute("SELECT pk FROM messages WHERE thread_id=?", (absorbed,)).fetchone()[0]

    def fails(c, merged):
        c.execute("UPDATE muted SET thread_id=? WHERE thread_id=?", (merged.survivor, merged.absorbed))
        raise sqlite3.OperationalError("disk full")
    monkeypatch.setattr(store, "_merged", fails)
    add(db, "<j@example.test>", refs="<p@example.test> <q@example.test>")
    db.commit()
    with pytest.raises(sqlite3.OperationalError):
        regroup.quiet(db)
    db.rollback()
    assert tid(db, p) == absorbed and everything_kept(db) == before
