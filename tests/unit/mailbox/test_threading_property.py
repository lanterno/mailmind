"""The property threading rests on: for any order of arrival, placing what each sync stored gives the same threads as
regrouping the whole account. Random reference graphs: mail that arrives before what it names, ids nobody holds, threads
that merge, a Gmail id that comes later, one Message-ID in several folders, and messages that go.

Each example runs in accounts of its own, through the store as a sync uses it. The suite runs a fixed set;
MAILMIND_THREADING_RUNS asks for that many with fresh seeds.
"""
import itertools
import os
import re

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

RUNS = int(os.environ.get("MAILMIND_THREADING_RUNS", "0"))
CASES = settings(max_examples=RUNS or 250, deadline=None, derandomize=not RUNS,
                 suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])

IDS = [f"<m{n}@example.test>" for n in range(8)]
NOBODY = ["<gone-1@example.test>", "<gone-2@example.test>", "<no-at-sign>"]        # named, and never held by a copy
GARBAGE = ["", "not-an-id", "<no-at-sign>", "<two words@example.test>"]
THRIDS = ["1781", "1799", "2001"]
FOLDERS = ["INBOX", "Archive", "[Gmail]/All Mail"]

named = st.sampled_from(IDS + NOBODY)
mail = st.fixed_dictionaries({
    "message_id": st.one_of(st.sampled_from(IDS), st.sampled_from(IDS), st.sampled_from(IDS), st.sampled_from(GARBAGE)),
    "in_reply_to": st.one_of(st.just(""), named),
    "refs": st.lists(named, max_size=3).map(" ".join),
    "gmail_thread": st.one_of(st.none(), st.none(), st.sampled_from(THRIDS)),
    "folder": st.sampled_from(FOLDERS),
    "ts": st.integers(0, 4),
})
syncs = st.lists(st.lists(mail, min_size=1, max_size=5), min_size=1, max_size=8)
_accounts = itertools.count(1)


class Mailbox:
    """One fictional account's mail, stored as `Ingest._process` stores it, with UIDs that name the same message in
    every account of an example."""

    def __init__(self, c, name):
        self.c, self.account = c, f"{name}{next(_accounts)}@example.test"
        self.next_uid = itertools.count(1)
        self.kept = []

    def store(self, batch, uids=None):
        from mailmind.bootstrap.mailbox import store
        uids = uids or [next(self.next_uid) for _ in batch]
        stored = []
        for uid, m in zip(uids, batch):
            pk = store.upsert_message(self.c, {
                "account": self.account, "kind": "gmail" if m["gmail_thread"] else "imap", "folder": m["folder"], "uid": uid,
                "message_id": m["message_id"], "gmail_thread": m["gmail_thread"], "from_addr": "lin@example.test",
                "from_name": "Lin", "subject": "Hello", "date_iso": None, "ts": m["ts"], "snippet": "",
                "bucket": "personal/protected", "is_junk": 0})
            if store.needs_threading(self.c, pk, m["in_reply_to"], m["refs"]):
                stored.append(pk)
            self.c.execute("UPDATE messages SET in_reply_to=?, refs=? WHERE pk=?", (m["in_reply_to"], m["refs"], pk))
        self.c.commit()
        return stored

    def place(self, batch):
        """What a sync does now: store it, then thread what it stored."""
        from mailmind.bootstrap.mailbox import store
        stored = self.store(batch)
        store.thread_stored(self.c, self.account, stored)
        self.c.commit()

    def regroup(self, batch=()):
        """What a sync did until now: store it, then regroup the whole account."""
        from mailmind.bootstrap.mailbox import store
        self.store(batch)
        store.recompute_threads(self.c, self.account)
        self.c.commit()

    def rows(self):
        return self.c.execute("SELECT pk, uid, message_id, gmail_thread, in_reply_to, refs, thread_id, folder, ts "
                              "FROM messages WHERE account=?", (self.account,)).fetchall()

    def ids(self):
        """Each message's thread id, by UID."""
        return {r["uid"]: r["thread_id"] for r in self.rows()}

    def links(self):
        return set(map(tuple, self.c.execute(
            "SELECT l.name, m.uid FROM thread_links l JOIN messages m ON m.pk=l.message_pk WHERE m.account=?", (self.account,))))

    def named_now(self):
        """What the links should say, by the same reading of the headers."""
        return {(name, r["uid"]) for r in self.rows() for name in what_it_names(r)}

    def plain(self, ids):
        """Thread ids with the account left out, to compare across accounts. A message that names nothing is left out too:
        it stands alone whatever happens, under a hash of its own row."""
        alone = {r["uid"] for r in self.rows() if not what_it_names(r)}
        return {uid: tid.replace(self.account, "<account>") for uid, tid in ids.items() if uid not in alone}

    def outside_two_gmail_threads(self):
        """Each message's id, but in a thread holding two Gmail threads: a whole recompute re-decides those at every
        sync and may flip with nothing merged, and threading what a sync stored leaves them be."""
        rows, thrids = self.rows(), {}
        for r in rows:
            if r["gmail_thread"]:
                thrids.setdefault(r["thread_id"], set()).add(r["gmail_thread"])
        return {r["uid"]: r["thread_id"] for r in rows if len(thrids.get(r["thread_id"], ())) < 2}


def what_it_names(r):
    """The ids a message's headers name, read here and not by the code under test."""
    own = (r["message_id"] or "").strip()
    names = {*re.findall(r"<[^<>\s]+>", r["in_reply_to"] or ""), *re.findall(r"<[^<>\s]+>", r["refs"] or "")}
    if re.fullmatch(r"<[^<>\s]+@[^<>\s]+>", own):
        names.add(own)
    if r["gmail_thread"]:
        names.add("gm:" + r["gmail_thread"])
    return names


def grouping(ids):
    """The threads, as sets of messages, whatever their ids."""
    groups = {}
    for uid, tid in ids.items():
        groups.setdefault(tid, set()).add(uid)
    return {frozenset(g) for g in groups.values()}


def components(rows):
    """What the mail itself says are threads, worked out here too."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            x = parent[x]
        return x
    for r in rows:
        find(("uid", r["uid"]))
        for name in what_it_names(r):
            parent[find(("name", name))] = find(("uid", r["uid"]))
    groups = {}
    for r in rows:
        groups.setdefault(find(("uid", r["uid"])), set()).add(r["uid"])
    return {frozenset(g) for g in groups.values()}


@pytest.fixture
def db(data_dir):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    c.execute("PRAGMA synchronous=OFF")                # thousands of commits, and no power cut to survive
    yield c
    c.close()


@pytest.fixture
def merges(monkeypatch):
    """Each ConversationsMerged heard, as (absorbed, survivor)."""
    from mailmind.bootstrap.mailbox import store
    real, heard = store._merged, []

    def spy(c, merged):
        heard.append((merged.absorbed, merged.survivor))
        real(c, merged)
    monkeypatch.setattr(store, "_merged", spy)
    return heard


def fresh(c):
    """An example's mail, in a database with no other's: all share the ids above, and one id's rows are what a lookup reads."""
    c.execute("DELETE FROM messages")
    c.execute("DELETE FROM muted")
    c.commit()


def assert_sticky(before, now, heard):
    """An id once given stays unless a merge absorbs it: then no message holds it any more, and the state went with it."""
    for uid, was in before.items():
        if uid in now and now[uid] != was:
            assert (was, now[uid]) in heard, f"{was} became {now[uid]} with no merge"
    for absorbed, _ in heard:
        assert absorbed not in now.values(), f"{absorbed} was absorbed and is still held"


@CASES
@given(arrivals=syncs, shuffled=st.randoms(use_true_random=False))
def test_any_order_of_arrival_gives_the_threads_a_whole_recompute_gives(db, merges, arrivals, shuffled):
    from mailmind.bootstrap.mailbox import store
    fresh(db)
    placed, regrouped = Mailbox(db, "placed"), Mailbox(db, "regrouped")
    for batch in arrivals:
        before = placed.ids()
        merges.clear()
        placed.place(batch)
        heard = list(merges)
        regrouped.regroup(batch)
        now = placed.ids()

        assert all(now.values()), "a message stored and not threaded"
        assert grouping(now) == grouping(regrouped.ids()) == components(placed.rows())
        assert_sticky(before, now, heard)
        assert placed.links() == placed.named_now()

        # And the very ids a whole recompute gives. Two Gmail threads merging are decided as it decides them.
        whole = regrouped.plain(regrouped.ids())
        same = placed.plain(placed.outside_two_gmail_threads())
        assert same == {uid: tid for uid, tid in whole.items() if uid in same}
        for absorbed, survivor in heard:
            if absorbed.startswith("gm:"):
                assert {whole[uid] for uid, tid in now.items() if tid == survivor} == {survivor.replace(placed.account, "<account>")}

    # The same mail in another order, all in one sync, is the same threads, and a first scan mints what it always minted.
    order = list(enumerate(m for batch in arrivals for m in batch))
    shuffled.shuffle(order)
    uids, other_order = [n + 1 for n, _ in order], [m for _, m in order]
    scanned, scanned_whole = Mailbox(db, "scanned"), Mailbox(db, "scanwhole")
    store.thread_stored(db, scanned.account, scanned.store(other_order, uids))
    scanned_whole.store(other_order, uids)
    store.recompute_threads(db, scanned_whole.account)
    db.commit()
    assert grouping(scanned.ids()) == grouping(scanned_whole.ids()) == grouping(placed.ids())
    assert scanned.plain(scanned.ids()) == scanned_whole.plain(scanned_whole.ids())

    # The repair finds nothing to do, but where it re-decides between two Gmail threads, which joins or splits nothing.
    settled, links, outside = placed.ids(), placed.links(), placed.outside_two_gmail_threads()
    placed.regroup()
    assert grouping(placed.ids()) == grouping(settled)
    assert placed.outside_two_gmail_threads() == outside
    assert placed.links() == links


@CASES
@given(arrivals=syncs, keep=st.lists(st.integers(0, 99), max_size=20))
def test_what_each_context_keeps_follows_its_thread(db, arrivals, keep):
    """Whatever merges, a mute is on a thread that holds messages, and on the one holding the message it was set on."""
    fresh(db)
    box = Mailbox(db, "kept")
    muted = set()                                      # the UID of the message each mute was set on
    for batch, choose in zip(arrivals, itertools.chain(keep, itertools.repeat(0))):
        box.place(batch)
        ids = box.ids()
        uid = sorted(ids)[choose % len(ids)]
        db.execute("INSERT OR IGNORE INTO muted(account, thread_id, muted_at) VALUES (?,?,?)", (box.account, ids[uid], "2026-11-01"))
        db.commit()
        muted.add(uid)
    ids = box.ids()
    held = {r[0] for r in db.execute("SELECT thread_id FROM muted WHERE account=?", (box.account,))}
    assert held <= set(ids.values())
    assert {ids[uid] for uid in muted} == held


@CASES
@given(steps=st.lists(st.one_of(st.lists(mail, min_size=1, max_size=4), st.integers(0, 99), st.tuples(st.integers(0, 99), named)),
                      min_size=1, max_size=12),
       gmail=st.booleans())
def test_mail_that_goes_leaves_threads_that_still_hold(db, merges, steps, gmail):
    """A copy can go at any time, and one stored again can name something new. Mail still joined by what it names stays
    one thread, the links follow the copy, and an id goes only to a merge. A split doesn't split: what was one thread
    keeps its one id. Without Gmail a whole recompute leaves it so too, and the repair has nothing to do. With it, the
    repair parts what no longer reaches a Gmail copy, and drops a gm: id with none left."""
    from mailmind.bootstrap.mailbox import store
    fresh(db)
    box = Mailbox(db, "going")
    for step in steps:
        before = box.ids()
        merges.clear()
        if isinstance(step, list):
            box.place([m if gmail else {**m, "gmail_thread": None} for m in step])
        elif before:
            uid = sorted(before)[(step if isinstance(step, int) else step[0]) % len(before)]
            row = next(r for r in box.rows() if r["uid"] == uid)
            if isinstance(step, int):
                store.delete_message(db, row["pk"])
                db.commit()
            else:                                      # a full rescan finds this UID naming something else
                again = {**dict(row), "refs": step[1]}
                store.thread_stored(db, box.account, box.store([again], [uid]))
                db.commit()
        rows, now = box.rows(), box.ids()
        assert all(now.values())
        for group in components(rows):
            assert len({now[uid] for uid in group}) == 1, "mail still joined holds two thread ids"
        assert_sticky(before, now, merges)
        assert box.links() == box.named_now()
    if not gmail:
        settled = box.ids()
        box.regroup()
        assert box.ids() == settled
