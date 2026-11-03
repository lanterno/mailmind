"""Conversation threading (threads.recompute_threads) on a temporary database: Gmail's thread id
first, then the Message-ID / In-Reply-To / References graph, then the message alone. A thread
never spans accounts and never forms on a subject. Once a thread has an id it keeps it as replies
arrive, because mutes, follow-ups and thread summaries are keyed on it; when two threads merge,
that state moves to the survivor.
"""
import itertools

import pytest

A, B = "ada@example.test", "bea@example.test"
_uid = itertools.count(1)


@pytest.fixture
def db(data_dir):
    import store
    c = store.conn()
    yield c
    c.close()


def add(c, mid, *, account=A, irt="", refs="", gm="", subject="Hello"):
    import store
    pk = store.upsert_message(c, {"account": account, "kind": "gmail" if gm else "imap", "folder": "INBOX",
                                  "uid": next(_uid), "message_id": mid, "gmail_thread": gm or None,
                                  "from_addr": "lin@example.test", "from_name": "Lin", "subject": subject,
                                  "date_iso": None, "ts": 0, "snippet": "", "bucket": "personal/protected",
                                  "is_junk": 0})
    c.execute("UPDATE messages SET in_reply_to=?, refs=? WHERE pk=?", (irt, refs, pk))
    return pk


def recompute(c, account=A):
    import threads
    threads.recompute_threads(c, account)
    c.commit()


def tid(c, pk):
    return c.execute("SELECT thread_id FROM messages WHERE pk=?", (pk,)).fetchone()["thread_id"]


def test_a_reply_joins_its_parent_by_in_reply_to_or_references(db):
    root = add(db, "<m@example.test>")
    by_irt = add(db, "<r1@example.test>", irt="<m@example.test>")
    by_refs = add(db, "<r2@example.test>", refs="<m@example.test> <r1@example.test>")
    recompute(db)
    assert tid(db, root) == tid(db, by_irt) == tid(db, by_refs)
    assert tid(db, root).startswith(f"rf:{A}:")


def test_a_reply_to_a_message_we_never_had_still_groups_its_siblings(db):
    a = add(db, "<x1@example.test>", irt="<missing@example.test>")
    b = add(db, "<x2@example.test>", refs="<missing@example.test>")
    recompute(db)
    assert tid(db, a) == tid(db, b)


def test_a_thread_keeps_its_id_when_a_reply_names_an_older_ancestor(db):
    root = add(db, "<m@example.test>")
    reply = add(db, "<r@example.test>", irt="<m@example.test>")
    recompute(db)
    first = tid(db, root)
    # The new reply's References start at <a@…>, which sorts before every id seen so far, so the
    # union-find root moves. The thread id must not.
    late = add(db, "<z@example.test>", refs="<a@example.test> <m@example.test> <r@example.test>")
    recompute(db)
    assert tid(db, root) == tid(db, reply) == tid(db, late) == first


def test_recomputing_changes_nothing(db):
    pks = [add(db, "<m@example.test>"), add(db, "<r@example.test>", irt="<m@example.test>"), add(db, "<s@example.test>")]
    recompute(db)
    before = [tid(db, pk) for pk in pks]
    db.execute("CREATE TEMP TRIGGER watch AFTER UPDATE OF thread_id ON messages "
               "BEGIN SELECT RAISE(ABORT, 'thread_id rewritten'); END")
    recompute(db)                                   # no UPDATE touches a row whose id is right
    assert [tid(db, pk) for pk in pks] == before


def test_two_threads_merge_into_the_smaller_id_and_carry_their_state(db):
    one = add(db, "<p@example.test>")
    two = add(db, "<q@example.test>")
    recompute(db)
    t1, t2 = tid(db, one), tid(db, two)
    keep, drop = min(t1, t2), max(t1, t2)
    db.execute("INSERT INTO muted(account, thread_id, muted_at) VALUES (?,?,?)", (A, drop, "2026-11-01"))
    db.execute("INSERT INTO followups(account, thread_id, kind, state) VALUES (?,?,?,?)", (A, drop, "sent", "pending"))
    db.execute("INSERT INTO thread_summaries(account, thread_id, fingerprint, tldr) VALUES (?,?,?,?)",
               (A, drop, "f-drop", "the dropped thread's summary"))
    db.execute("INSERT INTO thread_summaries(account, thread_id, fingerprint, tldr) VALUES (?,?,?,?)",
               (A, keep, "f-keep", "the survivor's summary"))
    joined = add(db, "<j@example.test>", refs="<p@example.test> <q@example.test>")
    recompute(db)
    assert tid(db, one) == tid(db, two) == tid(db, joined) == keep
    assert [r["thread_id"] for r in db.execute("SELECT thread_id FROM muted")] == [keep]
    assert [r["thread_id"] for r in db.execute("SELECT thread_id FROM followups")] == [keep]
    assert [(r["thread_id"], r["tldr"]) for r in db.execute("SELECT thread_id, tldr FROM thread_summaries")] == [
        (keep, "the survivor's summary")]               # on a clash the survivor's own row stays


def test_gmail_thread_ids_win_and_pull_in_header_replies(db):
    a = add(db, "<g1@example.test>", gm="1781")
    b = add(db, "<g2@example.test>", gm="1781")         # no headers linking them: Gmail says so
    c = add(db, "<g3@example.test>", irt="<g2@example.test>")
    other = add(db, "<g4@example.test>", gm="1799")
    recompute(db)
    assert tid(db, a) == tid(db, b) == tid(db, c) == f"gm:{A}:1781"
    assert tid(db, other) == f"gm:{A}:1799"


def test_threads_never_span_accounts(db):
    a = add(db, "<m@example.test>", account=A)
    b = add(db, "<r@example.test>", account=B, irt="<m@example.test>")
    bm = add(db, "<m@example.test>", account=B)
    recompute(db, A)
    recompute(db, B)
    assert tid(db, a).startswith(f"rf:{A}:") and tid(db, b).startswith(f"rf:{B}:")
    assert tid(db, b) == tid(db, bm) != tid(db, a)


def test_a_shared_subject_alone_is_not_a_thread(db):
    a = add(db, "<s1@example.test>", subject="Re: Invoice")
    b = add(db, "<s2@example.test>", subject="Re: Invoice")
    recompute(db)
    assert tid(db, a) != tid(db, b)


@pytest.mark.parametrize("mid", ["", "not-an-id", "<no-at-sign>", "<two words@example.test>"])
def test_a_message_without_a_usable_id_stands_alone(db, mid):
    a = add(db, mid)
    b = add(db, mid)
    reply = add(db, "<r@example.test>", irt=mid)
    recompute(db)
    assert len({tid(db, a), tid(db, b)}) == 2
    assert tid(db, reply) not in (tid(db, a), tid(db, b))
    assert all(tid(db, pk) for pk in (a, b, reply))
