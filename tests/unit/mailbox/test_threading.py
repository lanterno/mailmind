"""Threading what a sync stored (store.thread_stored, the Threader in the Mailbox's domain): what it decides, what it
reads and rewrites to do so, and that no sync takes the whole-account path. test_threads.py pins the whole-account path,
and test_threading_property.py holds the two to the same threads."""
import hashlib
import itertools
import re
import sqlite3

import pytest

from fixtures import world as w
from mailmind.mailbox.domain.threading import REFERENCES_MAX, Copy, Threader

A = "ada@example.test"
_uid = itertools.count(1)


# -- the Threader, with no database ---------------------------------------------

def place(arrivals, touching=None, members=()):
    threader = Threader(A)
    conversations = threader.conversations(arrivals, touching or {})
    return threader.place(conversations, members)


def minted(root):
    return f"rf:{A}:" + hashlib.sha1(root.encode()).hexdigest()[:16]


def test_new_mail_that_touches_nothing_mints_from_the_smallest_id_it_names():
    placed = place([Copy(1, "<r@example.test>", in_reply_to="<m@example.test>")])
    assert placed.given == {1: minted("<m@example.test>")} and placed.merged == {}


def test_a_message_with_no_usable_id_names_nothing_of_its_own():
    assert Copy(1, "not-an-id").names() == set()
    assert Copy(1, "<no-at-sign>", refs="<m@example.test>").names() == {"<m@example.test>"}
    assert place([Copy(7, "")]).given == {7: minted("pk:7")}


def test_a_reply_takes_the_thread_it_touches_and_merges_nothing():
    reply = Copy(1, "<r@example.test>", in_reply_to="<m@example.test>")
    placed = place([reply], {"<m@example.test>": {"rf:x"}})
    assert placed.given == {1: "rf:x"} and placed.merged == {}


def test_touching_two_threads_merges_them_into_the_smaller_id():
    joined = Copy(1, "<j@example.test>", refs="<p@example.test> <q@example.test>")
    placed = place([joined], {"<p@example.test>": {"rf:b"}, "<q@example.test>": {"rf:a"}})
    assert placed.given == {1: "rf:a"} and placed.merged == {"rf:b": "rf:a"}


def test_a_thread_that_gains_a_gmail_id_takes_it():
    placed = place([Copy(1, "<q@example.test>", "1781", in_reply_to="<p@example.test>")], {"<p@example.test>": {"rf:a"}})
    assert placed.given == {1: f"gm:{A}:1781"} and placed.merged == {"rf:a": f"gm:{A}:1781"}


def test_a_header_reply_joins_the_gmail_thread_it_touches():
    placed = place([Copy(1, "<q@example.test>", in_reply_to="<p@example.test>")], {"<p@example.test>": {f"gm:{A}:1781"}})
    assert placed.given == {1: f"gm:{A}:1781"} and placed.merged == {}


def test_a_gmail_thread_is_met_by_its_thrid_alone():
    placed = place([Copy(1, "<q@example.test>", "1781")], {"gm:1781": {f"gm:{A}:1781"}})
    assert placed.given == {1: f"gm:{A}:1781"} and placed.merged == {}


def test_two_gmail_threads_joined_by_headers_go_to_the_one_that_comes_later():
    """As a whole recompute decides it (test_hwf_two_gmail_threads_joined_by_headers_take_the_later_rows_id), not min()."""
    threader = Threader(A)
    arrival = Copy(9, "<j@example.test>", refs="<p@example.test> <q@example.test>", ts=300)
    touching = {"<p@example.test>": {f"gm:{A}:1781"}, "<q@example.test>": {f"gm:{A}:1799"}}
    conversations = threader.conversations([arrival], touching)
    assert threader.contested(conversations) == {f"gm:{A}:1781", f"gm:{A}:1799"}
    early = Copy(1, "<p@example.test>", "1781", thread_id=f"gm:{A}:1781", ts=200)
    late = Copy(2, "<q@example.test>", "1799", thread_id=f"gm:{A}:1799", ts=100)
    placed = threader.place(conversations, [late, early])
    assert placed.given == {9: f"gm:{A}:1781"} and placed.merged == {f"gm:{A}:1799": f"gm:{A}:1781"}


def test_one_gmail_id_is_not_contested():
    threader = Threader(A)
    arrival = Copy(9, "<j@example.test>", "1781", in_reply_to="<p@example.test>")
    assert threader.contested(threader.conversations([arrival], {"<p@example.test>": {f"gm:{A}:1781", "rf:a"}})) == set()


def test_arrivals_that_touch_one_thread_are_one_conversation_with_it():
    arrivals = [Copy(1, "<a@example.test>", in_reply_to="<p@example.test>"), Copy(2, "<b@example.test>", in_reply_to="<q@example.test>")]
    placed = place(arrivals, {"<p@example.test>": {"rf:x"}, "<q@example.test>": {"rf:x", "rf:y"}})
    assert placed.given == {1: "rf:x", 2: "rf:x"} and placed.merged == {"rf:y": "rf:x"}


def test_a_copy_placed_again_keeps_its_rf_id():
    assert place([Copy(1, "<a@example.test>", thread_id="rf:kept")]).given == {1: "rf:kept"}


def test_a_copy_placed_again_keeps_its_gmail_id_with_no_message_id_to_be_found_by():
    placed = place([Copy(1, "", refs="<elsewhere@example.test>", thread_id=f"gm:{A}:1781")])
    assert placed.given == {1: f"gm:{A}:1781"} and placed.merged == {}


def test_the_repair_trusts_a_gmail_id_only_while_a_gmail_copy_backs_it():
    gmail, elsewhere = f"gm:{A}:1781", minted("<elsewhere@example.test>")
    apart = Copy(2, "", refs="<elsewhere@example.test>", thread_id=gmail)
    placed = Threader(A).regroup([Copy(1, "<g@example.test>", "1781", thread_id=gmail), apart])
    assert placed.given == {1: gmail, 2: elsewhere} and placed.merged == {}
    placed = Threader(A).regroup([apart])
    assert placed.given == {2: elsewhere} and placed.merged == {gmail: elsewhere}


def test_a_forged_references_header_counts_for_its_nearest_ids():
    far = "<far@example.test>"
    near = [f"<n{i}@example.test>" for i in range(REFERENCES_MAX)]
    names = Copy(1, "<a@example.test>", refs=" ".join([far, *near])).names()
    assert far not in names and set(near) <= names and len(names) == REFERENCES_MAX + 1


# -- the store's path ----------------------------------------------------------

@pytest.fixture
def db(data_dir):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    yield c
    c.close()


def add(c, mid, *, account=A, irt="", refs="", gm="", folder="INBOX", ts=0):
    from mailmind.bootstrap.mailbox import store
    pk = store.upsert_message(c, {"account": account, "kind": "gmail" if gm else "imap", "folder": folder,
                                  "uid": next(_uid), "message_id": mid, "gmail_thread": gm or None,
                                  "from_addr": "lin@example.test", "from_name": "Lin", "subject": "Hello",
                                  "date_iso": None, "ts": ts, "snippet": "", "bucket": "personal/protected", "is_junk": 0})
    c.execute("UPDATE messages SET in_reply_to=?, refs=? WHERE pk=?", (irt, refs, pk))
    return pk


def thread(c, account=A, pks=()):
    from mailmind.bootstrap.mailbox import store
    store.thread_stored(c, account, pks)
    c.commit()


def tid(c, pk):
    return c.execute("SELECT thread_id FROM messages WHERE pk=?", (pk,)).fetchone()[0]


def links(c, pk):
    return sorted(r[0] for r in c.execute("SELECT name FROM thread_links WHERE message_pk=?", (pk,)))


def test_a_copy_threaded_has_the_links_of_what_it_names_and_they_go_with_it(db):
    from mailmind.bootstrap.mailbox import store
    pk = add(db, "<r@example.test>", irt="<m@example.test>", refs="<a@example.test> <m@example.test>", gm="1781")
    assert links(db, pk) == []                         # not threaded yet: none
    thread(db)
    assert links(db, pk) == ["<a@example.test>", "<m@example.test>", "<r@example.test>", "gm:1781"]
    store.delete_message(db, pk)
    db.commit()
    assert db.execute("SELECT count(*) FROM thread_links").fetchone()[0] == 0


def test_a_message_arriving_before_what_it_names_is_found_when_that_comes(db):
    reply = add(db, "<r@example.test>", irt="<m@example.test>")
    thread(db)
    was = tid(db, reply)
    root = add(db, "<m@example.test>")
    thread(db)
    assert tid(db, root) == tid(db, reply) == was


def test_two_siblings_of_a_message_nobody_has_are_one_thread(db):
    a = add(db, "<a@example.test>", irt="<gone@example.test>")
    thread(db)
    b = add(db, "<b@example.test>", refs="<gone@example.test>")
    thread(db)
    assert tid(db, a) == tid(db, b)


def test_the_same_message_in_another_folder_is_the_same_thread(db):
    inbox = add(db, "<m@example.test>")
    thread(db)
    archived = add(db, "<m@example.test>", folder="Archive")
    thread(db)
    assert tid(db, inbox) == tid(db, archived)


def test_two_threads_merge_rewriting_only_the_absorbed_ones_rows(db):
    p1, p2 = add(db, "<p1@example.test>"), add(db, "<p2@example.test>", irt="<p1@example.test>")
    q1, q2 = add(db, "<q1@example.test>"), add(db, "<q2@example.test>", irt="<q1@example.test>")
    other = add(db, "<other@example.test>")
    thread(db)
    keep, gone = sorted((tid(db, p1), tid(db, q1)))
    absorbed = {tid(db, pk): [pk, pk2] for pk, pk2 in ((p1, p2), (q1, q2))}[gone]
    db.execute("CREATE TEMP TABLE rewritten(pk)")
    db.execute("CREATE TEMP TRIGGER watch AFTER UPDATE OF thread_id ON messages BEGIN INSERT INTO rewritten VALUES (new.pk); END")
    joined = add(db, "<j@example.test>", refs="<p2@example.test> <q2@example.test>")
    thread(db, pks=[joined])
    assert {tid(db, pk) for pk in (p1, p2, q1, q2, joined)} == {keep} != {tid(db, other)}
    assert sorted(r[0] for r in db.execute("SELECT pk FROM rewritten")) == sorted([*absorbed, joined])


def test_a_reply_writes_only_its_own_row(db):
    root = add(db, "<m@example.test>")
    thread(db)
    db.execute("CREATE TEMP TABLE rewritten(pk)")
    db.execute("CREATE TEMP TRIGGER watch AFTER UPDATE OF thread_id ON messages BEGIN INSERT INTO rewritten VALUES (new.pk); END")
    reply = add(db, "<r@example.test>", irt="<m@example.test>")
    thread(db, pks=[reply])
    assert tid(db, reply) == tid(db, root)
    assert [r[0] for r in db.execute("SELECT pk FROM rewritten")] == [reply]


def test_a_thread_that_gains_a_gmail_id_rewrites_its_rows_and_takes_its_state_along(db):
    old = add(db, "<p@example.test>")
    thread(db)
    was = tid(db, old)
    db.execute("INSERT INTO muted(account, thread_id, muted_at) VALUES (?,?,?)", (A, was, "2026-11-01"))
    add(db, "<q@example.test>", gm="1781", irt="<p@example.test>")
    thread(db)
    assert tid(db, old) == f"gm:{A}:1781"
    assert [r[0] for r in db.execute("SELECT thread_id FROM muted")] == [f"gm:{A}:1781"]


def test_two_gmail_threads_end_as_a_whole_recompute_ends_them(db):
    from mailmind.bootstrap.mailbox import store
    a = add(db, "<g1@example.test>", gm="1781", ts=100)
    thread(db)
    b = add(db, "<g2@example.test>", gm="1799", ts=200, irt="<g1@example.test>")
    thread(db)
    assert tid(db, a) == tid(db, b) == f"gm:{A}:1799"
    store.recompute_threads(db, A)
    db.commit()
    assert tid(db, a) == tid(db, b) == f"gm:{A}:1799"


def test_a_thread_holding_two_gmail_threads_keeps_its_id_when_a_copy_changes_nothing(db):
    """A whole recompute may flip here, since the new copy changes which thread comes first. Nothing merged, so the id stays."""
    a = add(db, "<g1@example.test>", gm="1781", ts=100)
    b = add(db, "<g2@example.test>", gm="1799", ts=200, irt="<g1@example.test>")
    thread(db)
    assert tid(db, b) == f"gm:{A}:1799"
    earlier = add(db, "<g3@example.test>", gm="1799", ts=50)
    thread(db)
    assert tid(db, a) == tid(db, b) == tid(db, earlier) == f"gm:{A}:1799"


def test_threads_stay_in_their_account(db):
    from mailmind.bootstrap.mailbox import store
    other = "bea@example.test"
    theirs = add(db, "<m@example.test>", account=other)
    thread(db, other)
    ours = add(db, "<r@example.test>", irt="<m@example.test>")
    thread(db)
    assert tid(db, theirs).startswith(f"rf:{other}:") and tid(db, ours).startswith(f"rf:{A}:")
    store.recompute_threads(db, other)
    assert tid(db, theirs).startswith(f"rf:{other}:")


def test_a_second_pass_changes_nothing(db):
    pks = [add(db, "<m@example.test>"), add(db, "<r@example.test>", irt="<m@example.test>")]
    thread(db)
    before = [tid(db, pk) for pk in pks]
    db.execute("CREATE TEMP TRIGGER watch AFTER UPDATE OF thread_id ON messages BEGIN SELECT RAISE(ABORT, 'rewritten'); END")
    thread(db, pks=pks)
    assert [tid(db, pk) for pk in pks] == before


def test_a_message_named_differently_when_stored_again_is_placed_again(db):
    from mailmind.bootstrap.mailbox import store
    a, b = add(db, "<a@example.test>"), add(db, "<b@example.test>")
    thread(db)
    assert tid(db, a) != tid(db, b)
    assert store.needs_threading(db, a, "", "") is False
    assert store.needs_threading(db, a, "", "<b@example.test>") is True
    db.execute("UPDATE messages SET refs='<b@example.test>' WHERE pk=?", (a,))
    thread(db, pks=[a])
    assert tid(db, a) == tid(db, b)
    assert "<b@example.test>" in links(db, a)


def test_a_copy_with_no_message_id_stays_in_its_gmail_thread_when_it_names_something_else(db):
    """It has no id of its own for its links to find its thread by, and an id goes only to a merge."""
    gmail = add(db, "<g@example.test>", gm="1781")
    other = add(db, "", refs="<g@example.test>")
    thread(db)
    db.execute("UPDATE messages SET refs='<elsewhere@example.test>' WHERE pk=?", (other,))
    thread(db, pks=[other])
    assert tid(db, other) == tid(db, gmail) == f"gm:{A}:1781"
    assert links(db, other) == ["<elsewhere@example.test>"]


def test_a_mute_stays_with_the_last_copy_of_a_gmail_thread_when_it_names_something_else(db):
    """Nothing else holds the id then, so the copy leaving it unannounced would strand the mute. The repair moves both."""
    from mailmind.bootstrap.mailbox import store
    gmail = add(db, "<g@example.test>", gm="1781")
    other = add(db, "", refs="<g@example.test>")
    thread(db)
    db.execute("INSERT INTO muted(account, thread_id, muted_at) VALUES (?,?,?)", (A, f"gm:{A}:1781", "2026-11-01"))
    store.delete_message(db, gmail)
    db.execute("UPDATE messages SET refs='<elsewhere@example.test>' WHERE pk=?", (other,))
    thread(db, pks=[other])
    muted = lambda: [r[0] for r in db.execute("SELECT thread_id FROM muted")]
    assert tid(db, other) == f"gm:{A}:1781" and muted() == [tid(db, other)]
    store.recompute_threads(db, A)
    db.commit()
    assert tid(db, other) == minted("<elsewhere@example.test>") and muted() == [tid(db, other)]


def test_with_nothing_to_do_no_transaction_opens(db):
    add(db, "<m@example.test>")
    thread(db)
    from mailmind.bootstrap.mailbox import store
    store.thread_stored(db, A, [])
    assert not db.in_transaction


# -- what it reads --------------------------------------------------------------

def scans(c, statements):
    """The tables any of these statements walks instead of looking up."""
    found = set()
    for sql in statements:
        if not re.match(r"\s*(SELECT|UPDATE|DELETE|INSERT)", sql):
            continue
        for row in c.execute("EXPLAIN QUERY PLAN " + sql):
            detail = row[3]
            if "SCAN" in detail and "CONSTANT" not in detail:
                found.add(detail)
            if detail.startswith("SEARCH messages") and "account=?" in detail and "folder" not in detail and "pk" not in detail:
                found.add(detail)                       # by account alone is the whole account
    return found


def test_a_pass_finds_everything_through_an_index(db):
    from mailmind.bootstrap.mailbox import store
    for n in range(50):
        add(db, f"<q{n}@example.test>", irt=f"<q{n - 1}@example.test>" if n % 3 else "")
    add(db, "<g1@example.test>", gm="1781", ts=100)
    add(db, "<g2@example.test>", gm="1799", ts=200)
    thread(db)
    add(db, "<reply@example.test>", irt="<q1@example.test>", refs="<q3@example.test> <q6@example.test>")
    joined = add(db, "<j@example.test>", refs="<g1@example.test> <g2@example.test>")
    statements = []
    db.commit()
    db.set_trace_callback(statements.append)
    store.thread_stored(db, A, [joined])
    db.set_trace_callback(None)
    db.commit()
    assert len(statements) > 5 and not scans(db, statements)
    # Without an index to find mail with no thread id yet, it would walk the account on every sync.
    assert any("thread_id IS NULL" in s for s in statements)


# -- through a sync ------------------------------------------------------------

@pytest.fixture
def thread_of(world):
    def of(mid, account=w.ADA):
        return world_rows(account, mid)[0]["thread_id"]
    return of


def world_rows(account, mid):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute("SELECT * FROM messages WHERE account=? AND message_id=?", (account, mid))]
    finally:
        c.close()


def reply_to(parent, mid):
    return (f"From: Lin <lin@example.test>\r\nTo: {w.ADA}\r\nSubject: Re: Hello\r\nDate: Mon, 02 Nov 2026 09:00:00 +0000\r\n"
            f"Message-ID: {mid}\r\nIn-Reply-To: {parent}\r\nReferences: {parent}\r\n\r\nThursday works.\r\n").encode()


def test_no_sync_regroups_the_whole_account(world, thread_of, monkeypatch):
    from mailmind.bootstrap.mailbox import store

    def refuses(c, account):
        raise AssertionError("a sync took the whole-account path")
    monkeypatch.setattr(store, "recompute_threads", refuses)
    parent = w.message_id("ada-inbox-01-lunch.eml")
    world.deliver(w.ADA, "INBOX", reply_to(parent, "<late-reply@example.test>"))
    assert thread_of("<late-reply@example.test>") == thread_of(parent)


def test_a_sync_hands_threading_only_what_it_stored(world, monkeypatch):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest, store
    real, heard = store.thread_stored, []

    def spy(c, account, pks=()):
        heard.append(list(pks))
        return real(c, account, pks)
    monkeypatch.setattr(store, "thread_stored", spy)
    ingest.sync_folder(accounts.get(w.ADA), "INBOX")               # nothing new
    world.deliver(w.ADA, "INBOX", reply_to(w.message_id("ada-inbox-01-lunch.eml"), "<late-reply@example.test>"))
    pk = world_rows(w.ADA, "<late-reply@example.test>")[0]["pk"]
    assert heard == [[], [pk]]
    heard.clear()
    ingest.sync_folder(accounts.get(w.ADA), "INBOX", full=True)    # every UID stored again, and nothing to thread
    assert heard == [[]]


def test_mail_a_sync_stored_and_never_threaded_goes_with_the_next_one(world, thread_of, monkeypatch):
    from mailmind.bootstrap.mailbox import store
    real = store.thread_stored

    def dies(c, account, pks=()):
        raise OSError("the power went")
    parent = w.message_id("ada-inbox-01-lunch.eml")
    monkeypatch.setattr(store, "thread_stored", dies)
    with pytest.raises(OSError):
        world.deliver(w.ADA, "INBOX", reply_to(parent, "<first@example.test>"))
    assert world_rows(w.ADA, "<first@example.test>")[0]["thread_id"] is None
    monkeypatch.setattr(store, "thread_stored", real)
    world.deliver(w.ADA, "INBOX", reply_to("<first@example.test>", "<second@example.test>"))
    assert thread_of("<first@example.test>") == thread_of("<second@example.test>") == thread_of(parent)


def test_a_merge_that_fails_in_a_sync_holds_no_lock_and_the_next_sync_does_it(world, thread_of, monkeypatch):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest, store
    a, b = w.message_id("ada-inbox-03-newsletter.eml"), w.message_id("ada-inbox-05-receipt.eml")
    assert thread_of(a) != thread_of(b)
    joined = (f"From: Lin <lin@example.test>\r\nTo: {w.ADA}\r\nSubject: Both\r\nDate: Mon, 02 Nov 2026 09:00:00 +0000\r\n"
              f"Message-ID: <both@example.test>\r\nReferences: {a} {b}\r\n\r\nBoth of them.\r\n").encode()
    real = store._merged

    def fails(c, merged):
        raise sqlite3.OperationalError("disk full")
    monkeypatch.setattr(store, "_merged", fails)
    with pytest.raises(sqlite3.OperationalError):
        world.deliver(w.ADA, "INBOX", joined)
    c = sqlite3.connect(world.dir / "mailmind.db", timeout=0)
    c.execute("UPDATE sync_state SET last_sync=last_sync")       # no waiting: the write lock is free
    c.commit()
    c.close()
    assert world_rows(w.ADA, "<both@example.test>")[0]["thread_id"] is None and thread_of(a) != thread_of(b)
    monkeypatch.setattr(store, "_merged", real)
    ingest.sync_folder(accounts.get(w.ADA), "INBOX")
    assert thread_of("<both@example.test>") == thread_of(a) == thread_of(b)


def test_mail_the_command_line_stored_goes_with_the_next_sync(world, thread_of):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    world.server.deliver(w.ADA, "INBOX", reply_to(w.message_id("ada-inbox-01-lunch.eml"), "<by-hand@example.test>"))
    ingest.ingest_account(accounts.get(w.ADA), 3650)               # `mailmind sync` threads nothing
    assert world_rows(w.ADA, "<by-hand@example.test>")[0]["thread_id"] is None
    ingest.sync_folder(accounts.get(w.ADA), "Archive")
    assert thread_of("<by-hand@example.test>") == thread_of(w.message_id("ada-inbox-01-lunch.eml"))


def test_undo_taking_a_message_back_threads_it(world, thread_of):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    parent = w.message_id("ada-inbox-01-lunch.eml")
    world.server.deliver(w.ADA, "INBOX", reply_to(parent, "<undone@example.test>"))
    assert ingest.ingest_message(accounts.get(w.ADA), "INBOX", "<undone@example.test>") == 1
    assert thread_of("<undone@example.test>") == thread_of(parent)


# -- mail stored before the links were ----------------------------------------

def test_mail_threaded_before_migration_42_is_found_by_mail_that_comes_after(tmp_path):
    from mailmind.bootstrap.mailbox import store
    from mailmind.platform import settings
    from mailmind.platform.db import migrate, migrations
    from mailmind.platform.db.migrations import m0042_thread_links as m
    with settings.use(settings.Settings(home=str(tmp_path))):
        migrate.init(settings.current().db, migrations.MIGRATIONS[:41])
        c = store.conn()
        ins = ("INSERT INTO messages(account, kind, folder, uid, message_id, in_reply_to, refs, gmail_thread, thread_id, subject, from_addr) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?)")
        root = c.execute(ins, (A, "imap", "INBOX", 1, "<m@example.test>", "", "", None, "rf:old", "Hi", "lin@example.test")).lastrowid
        unthreaded = c.execute(ins, (A, "imap", "INBOX", 2, "<u@example.test>", "", "", None, None, "Hi", "lin@example.test")).lastrowid
        row = (A, "imap", "INBOX", 3, " <g@example.test> ", "<i@example.test>", "<x@example.test> <i@example.test>", "1781", f"gm:{A}:1781", "Hi", "lin@example.test")
        gmail = c.execute(ins, row).lastrowid
        c.commit()
        c.close()
        store.init()
        c = store.conn()
        try:
            assert links(c, root) == ["<m@example.test>"] and links(c, unthreaded) == []
            assert links(c, gmail) == sorted(Copy(gmail, row[4], row[7], row[5], row[6]).names())
            assert m._names(row[4], row[7], row[5], row[6]) == Copy(gmail, row[4], row[7], row[5], row[6]).names()
            reply = add(c, "<r@example.test>", irt="<m@example.test>")
            thread(c)
            assert tid(c, reply) == "rf:old" and tid(c, unthreaded)
        finally:
            c.close()
