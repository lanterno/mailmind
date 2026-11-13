"""Threading, now decided by the Rust core, holds to what the Python app decided before it: the same names, the same
conversations and the same thread ids, in the same order, or every thread the database already holds would be
split, merged or renamed under the person reading it.

`python` below is that code as it stood, kept here as the oracle. core/crates/mm-mailbox/tests/threading.rs pins a
few of its answers on the Rust side.
"""
import hashlib
import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mailmind.mailbox.domain import threading


# -- the oracle: mailmind/mailbox/domain/threading.py before the Rust core decided ------------------------------

def _oracle():
    _ID = re.compile(r"<[^<>\s]+>")
    _WELL = re.compile(r"^<[^<>\s]+@[^<>\s]+>$")
    _GMAIL_NAME = "gm:"
    _THREAD_NODE = "\0"
    REFERENCES_MAX = 1000

    def _ids(blob):
        return _ID.findall(blob or "")

    @dataclass(frozen=True, slots=True)
    class Copy:
        pk: int
        message_id: str | None = None
        gmail_thread: str | None = None
        in_reply_to: str | None = None
        refs: str | None = None
        thread_id: str | None = None
        folder: str = ""
        ts: int | None = None

        @property
        def thrid(self):
            return (self.gmail_thread or "").strip()

        @property
        def key(self):
            mid = (self.message_id or "").strip()
            return mid if _WELL.match(mid) else f"pk:{self.pk}"

        def references(self):
            return _ids(self.in_reply_to) + _ids(self.refs)[-REFERENCES_MAX:]

        def names(self):
            names = set(self.references())
            if self.key.startswith("<"):
                names.add(self.key)
            if self.thrid:
                names.add(_GMAIL_NAME + self.thrid)
            return names

    def _in_store_order(copies):
        return sorted(copies, key=lambda m: (m.folder, m.ts is not None, m.ts or 0, m.pk))

    class _UF:
        def __init__(self):
            self.p = {}

        def find(self, x):
            self.p.setdefault(x, x)
            while self.p[x] != x:
                self.p[x] = self.p[self.p[x]]
                x = self.p[x]
            return x

        def union(self, a, b):
            ra, rb = self.find(a), self.find(b)
            if ra == rb:
                return
            if rb < ra:
                ra, rb = rb, ra
            self.p[rb] = ra

    @dataclass(slots=True)
    class Conversation:
        root: str
        arrivals: list = field(default_factory=list)
        held: set = field(default_factory=set)

    @dataclass(frozen=True, slots=True)
    class Placement:
        given: dict
        merged: dict

    class Threader:
        def __init__(self, account):
            self._account = account

        def _gmail_id(self, thrid):
            return f"gm:{self._account}:{thrid}"

        def _minted(self, root):
            h = hashlib.sha1(root.encode("utf-8", "replace")).hexdigest()[:16]
            return f"rf:{self._account}:{h}"

        def conversations(self, arrivals: Iterable, touching: Mapping[str, Collection[str]]):
            arrivals = list(arrivals)
            uf = _UF()
            for m in arrivals:
                uf.union(m.key, m.key)
                for name in m.names():
                    uf.union(m.key, name)
            for name, ids in touching.items():
                for tid in ids:
                    uf.union(name, _THREAD_NODE + tid)
            found = {}
            for m in arrivals:
                root = uf.find(m.key)
                conversation = found.setdefault(root, Conversation(root))
                conversation.arrivals.append(m)
                if m.thread_id:
                    conversation.held.add(m.thread_id)
            for name, ids in touching.items():
                conversation = found.get(uf.find(name))
                if conversation:
                    conversation.held.update(ids)
            return list(found.values())

        def _gmail_ids(self, conversation):
            return ({self._gmail_id(m.thrid) for m in conversation.arrivals if m.thrid}
                    | {tid for tid in conversation.held if tid.startswith("gm:")})

        def contested(self, conversations):
            return {tid for conversation in conversations if len(self._gmail_ids(conversation)) > 1
                    for tid in conversation.held if tid.startswith("gm:")}

        def _survivor(self, conversation, members=()):
            gmail = self._gmail_ids(conversation)
            if len(gmail) > 1:
                copies = [*conversation.arrivals, *(m for m in members if m.thread_id in conversation.held)]
                thrids = list(dict.fromkeys(m.thrid for m in _in_store_order(copies) if m.thrid))
                return self._gmail_id(thrids[-1]) if thrids else min(gmail)
            if gmail:
                return gmail.pop()
            if conversation.held:
                return min(conversation.held)
            return self._minted(conversation.root)

        def place(self, conversations, members=()):
            members = list(members)
            given = {}
            merged = {}
            for conversation in conversations:
                survivor = self._survivor(conversation, members)
                given.update((m.pk, survivor) for m in conversation.arrivals)
                merged.update((tid, survivor) for tid in conversation.held if tid != survivor)
            return Placement(given, merged)

        def regroup(self, copies):
            rows = _in_store_order(copies)
            given = {}
            for conversation in self.conversations(rows, {}):
                conversation.held = {tid for tid in conversation.held if tid.startswith("rf:")}
                survivor = self._survivor(conversation)
                given.update((m.pk, survivor) for m in conversation.arrivals)
            held = set(given.values())
            merged = {}
            for m in rows:
                if m.thread_id and m.thread_id not in held:
                    merged.setdefault(m.thread_id, given[m.pk])
            return Placement(given, merged)

    return SimpleNamespace(Copy=Copy, Conversation=Conversation, Threader=Threader, REFERENCES_MAX=REFERENCES_MAX)


python = _oracle()


# -- what the mail can say --------------------------------------------------------------------------------------

A = "ada@example.test"
SPACES = "".join(chr(c) for c in range(0x110000) if chr(c).isspace())   # Python's whitespace, U+001C to U+001F among it
IDS = ["<m0@example.test>", "<m1@example.test>", "<m2@example.test>", "<m3@example.test>",
       "<café@exämple.test>", "<Ünï.☕@example.test>", "<M0@EXAMPLE.TEST>"]
NOBODY = ["<gone-1@example.test>", "<gone-2@example.test>"]                    # named, and never held by a copy
GARBAGE = ["", "not-an-id", "<no-at-sign>", "<two words@example.test>", "<two\x1cwords@example.test>", "<@example.test>",
           "<m0@>", "<a@@b>", "<<m0@example.test>>", "<m0@example.test", "m0@example.test>", "<m0@example.test>\n",
           "<a​b@example.test>", "<\0@example.test>"]
THRIDS = ["", " ", "1781", " 1781\n", "1799", "　1799\x1f", "2001", "x\x1cy"]
THREADS = ["", "rf:a", "rf:b", "rf:x", "rf:é", f"gm:{A}:1781", f"gm:{A}:1799", f"gm:{A}:2001", "gm:elsewhere", "\0odd", "odd"]

padding = st.text(alphabet=SPACES, max_size=2)
own_id = st.one_of(st.none(), st.sampled_from(IDS + GARBAGE),
                   st.builds(lambda before, mid, after: before + mid + after, padding, st.sampled_from(IDS + GARBAGE), padding),
                   st.text(max_size=12))
token = st.one_of(st.sampled_from(IDS + NOBODY + GARBAGE), st.text(alphabet="<>@ab.é\x1c　​", max_size=8))
between = st.sampled_from(["", " ", ",", "\t", "\r\n ", "\x1c", "\x1f", "　", "​", "\x85", "\xa0", "x"])
header = st.lists(st.tuples(token, between), max_size=6).map(lambda parts: "".join(t + s for t, s in parts))
# A forged References header around the 1000 ids that count, its first id in the pool so the cut decides a join.
forged = st.builds(lambda first, n: " ".join([first, *(f"<r{i}@example.test>" for i in range(n))]),
                   st.sampled_from(IDS), st.integers(995, 1003))
headers = st.one_of(st.none(), header, header, header, st.text(max_size=20))

pk = st.one_of(st.integers(1, 6), st.integers(-2**63, 2**63 - 1))
ts = st.one_of(st.none(), st.integers(-1, 3), st.integers(-2**63, 2**63 - 1))
folder = st.sampled_from(["INBOX", "Archive", "Archivé", "[Gmail]/All Mail", "", "inbox", "Z"])
copy = st.tuples(pk, own_id, st.one_of(st.none(), st.sampled_from(THRIDS)), headers, headers,
                 st.one_of(st.none(), st.sampled_from(THREADS), st.text(max_size=6)), folder, ts)
long_copy = st.tuples(pk, own_id, st.none(), headers, forged, st.one_of(st.none(), st.sampled_from(THREADS)), folder, ts)
copies = st.lists(st.one_of(copy, copy, copy, copy, copy, copy, copy, copy, copy, long_copy), max_size=12)

name = st.one_of(st.sampled_from(IDS + NOBODY + [f"gm:{t.strip()}" for t in THRIDS if t.strip()]), st.text(max_size=6))
tid = st.one_of(st.sampled_from(THREADS), st.text(max_size=6))
touching = st.dictionaries(name, st.one_of(st.sets(tid, max_size=3), st.lists(tid, max_size=3)), max_size=5)
account = st.sampled_from([A, "bé@example.test"])

CASES = settings(max_examples=1000, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])


def both(rows):
    return [python.Copy(*r) for r in rows], [threading.Copy(*r) for r in rows]


def shape(conversations, arrivals):
    """Conversations as comparable data: each one's root, where its copies stand among the arrivals, and its ids."""
    at = {id(m): n for n, m in enumerate(arrivals)}
    return [(c.root, [at[id(m)] for m in c.arrivals], c.held) for c in conversations]


def items(placement):
    return list(placement.given.items()), list(placement.merged.items())


# -- the properties ---------------------------------------------------------------------------------------------

@CASES
@given(copies)
def test_rust_reads_every_copy_as_python_did(rows):
    for old, new in zip(*both(rows)):
        assert (new.thrid, new.key, new.references(), new.names()) == (old.thrid, old.key, old.references(), old.names())


@CASES
@given(account, copies, touching, copies)
def test_rust_places_every_sync_as_python_did(who, arrivals, touched, members):
    (old, new), (old_members, new_members) = both(arrivals), both(members)
    old_threader, new_threader = python.Threader(who), threading.Threader(who)
    old_conversations = old_threader.conversations(old, touched)
    new_conversations = new_threader.conversations(new, touched)
    assert shape(new_conversations, new) == shape(old_conversations, old)
    assert new_threader.contested(new_conversations) == old_threader.contested(old_conversations)

    # Given the very conversations Python found, with their sets, Rust answers in the order Python did: `merged` follows
    # each `held` set's own order, and the rest follows the arrivals.
    same = [threading.Conversation(c.root, [new[n] for n in at], c.held)
            for c, (_, at, _) in zip(old_conversations, shape(old_conversations, old))]
    assert items(new_threader.place(same, new_members)) == items(old_threader.place(old_conversations, old_members))

    # From its own conversations, `merged` may list one conversation's absorbed ids in another order, as two Python
    # processes would, with a hash seed each. What each id goes to is the same.
    placed, was = new_threader.place(new_conversations, new_members), old_threader.place(old_conversations, old_members)
    assert list(placed.given.items()) == list(was.given.items()) and placed.merged == was.merged


@CASES
@given(account, copies)
def test_rust_regroups_every_account_as_python_did(who, rows):
    old, new = both(rows)
    assert items(threading.Threader(who).regroup(new)) == items(python.Threader(who).regroup(old))


# -- what the generator might not reach -------------------------------------------------------------------------

def test_the_references_that_count_are_as_many_as_python_counted():
    assert threading.REFERENCES_MAX == python.REFERENCES_MAX
    refs = " ".join(f"<r{i:04}@example.test>" for i in range(python.REFERENCES_MAX + 5))
    old, new = both([(1, "<a@example.test>", None, "<p@example.test>", refs, None, "", None)])
    assert new[0].references() == old[0].references() and len(new[0].names()) == python.REFERENCES_MAX + 2


def test_every_character_python_splits_at_ends_an_id():
    rows = [(1, f"<a{c}b@example.test>", f"{c}1781{c}", f"<p@example.test>{c}<q{c}@example.test>", None, None, "", None)
            for c in SPACES]
    for old, new in zip(*both(rows)):
        assert (new.key, new.thrid, new.names()) == (old.key, old.thrid, old.names()) == ("pk:1", "1781", {"<p@example.test>", "gm:1781"})


@pytest.mark.parametrize("root", ["<m0@example.test>", "pk:7", "<café@exämple.test>", "<" + "x" * 52 + "@example.test>",
                                  "<" + "y" * 200 + "@example.test>"])
def test_a_new_thread_is_minted_from_the_sha_1_of_its_root(root):
    row = (7, root if root.startswith("<") else "", None, None, None, None, "", None)
    old, new = both([row])
    assert threading.Threader(A).regroup(new).given == python.Threader(A).regroup(old).given
    assert threading.Threader(A).regroup(new).given[7] == f"rf:{A}:" + hashlib.sha1(root.encode()).hexdigest()[:16]


@pytest.mark.parametrize("where", [1, 2, 3, 4, 5, 6])
def test_text_that_isnt_unicode_is_refused(where):
    """A lone surrogate is what an undecodable header byte becomes. Python threaded it, but the database refuses one in
    any column, so no copy read from it ever held one: Rust refuses it where it crosses."""
    row = [1, "<a@example.test>", "1781", "<p@example.test>", "<q@example.test>", "rf:a", "INBOX", 0]
    row[where] = "caf\udce9"
    with pytest.raises(UnicodeEncodeError):
        threading.Threader(A).regroup([threading.Copy(*row)])
