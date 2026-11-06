"""Conservative, account-scoped conversation threading for MailMind (stdlib only).

A Threader gives each message of an account its thread id, in precedence order:
  1) Gmail X-GM-THRID  — same account + same thrid = one thread (most reliable),
  2) a union-find over the Message-ID / In-Reply-To / References graph (JWZ-lite),
  3) the message's own id (singleton).
It NEVER threads on subject, NEVER spans accounts, and gives every row a non-NULL
thread_id. The rf: thread_id is derived from the component's union-find ROOT (the
lexicographically-smallest id in it, the ids its rows only refer to included), NOT the whole
member set, so a thread keeps the SAME id as replies arrive, and mints the same one again when
it is synced whole after every copy went: its root is the same whichever rows were there first.

Two ways in, and for mail that only arrives they end with the same threads under the same ids:
  `conversations` and `place` take what just arrived and the ids of the threads it touches, which
  is all a sync has to read: everything a thread holds is one thread already.
  `regroup` takes every copy the account holds. It is the repair.

An id is sticky once given, and goes only when a merge absorbs it (two threads joined, or a thread
that gained a Gmail thread id). The survivor is a Gmail id over an rf: id, the smallest of several
rf: ids, and of two Gmail threads joined by headers the one that comes later in store order. Whoever
keeps state under the absorbed id moves it in the same transaction (events.ConversationsMerged).
"""
import hashlib
import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field

_ID = re.compile(r"<[^<>\s]+>")
_WELL = re.compile(r"^<[^<>\s]+@[^<>\s]+>$")
_GMAIL_NAME = "gm:"          # how a copy names its Gmail thread: no Message-ID starts so
_THREAD_NODE = "\0"          # a thread among the ids in the union-find: no id starts so, and nothing mints from one

REFERENCES_MAX = 1000        # how many of a References header count, nearest first: a forged one can be as long as it likes


def _ids(blob: str | None) -> list[str]:
    return _ID.findall(blob or "")


@dataclass(frozen=True, slots=True)
class Copy:
    """What threading reads of one stored copy of a message."""
    pk: int
    message_id: str | None = None
    gmail_thread: str | None = None
    in_reply_to: str | None = None
    refs: str | None = None
    thread_id: str | None = None
    folder: str = ""
    ts: int | None = None

    @property
    def thrid(self) -> str:
        return (self.gmail_thread or "").strip()

    @property
    def key(self) -> str:
        """Its own id, or a garbage or blank one's stand-in, which no other copy can name."""
        mid = (self.message_id or "").strip()
        return mid if _WELL.match(mid) else f"pk:{self.pk}"

    def references(self) -> list[str]:
        """The ids it says it follows."""
        return _ids(self.in_reply_to) + _ids(self.refs)[-REFERENCES_MAX:]

    def names(self) -> set[str]:
        """Everything another copy can meet it by: two copies that share a name are one thread."""
        names = set(self.references())
        if self.key.startswith("<"):
            names.add(self.key)
        if self.thrid:
            names.add(_GMAIL_NAME + self.thrid)
        return names


def _in_store_order(copies: Iterable[Copy]) -> list[Copy]:
    """By folder, date and row: which of two Gmail threads comes later is decided in this order."""
    return sorted(copies, key=lambda m: (m.folder, m.ts is not None, m.ts or 0, m.pk))


class _UF:
    """Disjoint-set with a deterministic (lexicographically-smaller) root."""
    def __init__(self) -> None:
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if rb < ra:
            ra, rb = rb, ra
        self.p[rb] = ra


@dataclass(slots=True)
class Conversation:
    """Copies placed together: `arrivals`, and `held`, the ids of the threads they touch."""
    root: str
    arrivals: list[Copy] = field(default_factory=lambda: list[Copy]())
    held: set[str] = field(default_factory=lambda: set[str]())


@dataclass(frozen=True, slots=True)
class Placement:
    """`given`: each copy's thread id now, by pk. `merged`: each id absorbed, and the id that absorbed it."""
    given: dict[int, str]
    merged: dict[str, str]


class Threader:
    """Decides the thread ids of one account's mail, and holds no state of its own."""

    def __init__(self, account: str) -> None:
        self._account = account

    def _gmail_id(self, thrid: str) -> str:
        return f"gm:{self._account}:{thrid}"

    def _minted(self, root: str) -> str:
        h = hashlib.sha1(root.encode("utf-8", "replace")).hexdigest()[:16]
        return f"rf:{self._account}:{h}"

    def conversations(self, arrivals: Iterable[Copy], touching: Mapping[str, Collection[str]]) -> list[Conversation]:
        """Copies that belong together, with the threads they touch: `touching` says, for each name the arrivals
        name, the ids of the threads that hold a copy naming it. Two threads touched by one conversation are one
        thread from now on. An arrival's own rf: id counts as touched, for one whose references changed."""
        arrivals = list(arrivals)
        uf = _UF()
        for m in arrivals:
            uf.union(m.key, m.key)
            for name in m.names():
                uf.union(m.key, name)
        for name, ids in touching.items():
            for tid in ids:
                uf.union(name, _THREAD_NODE + tid)
        found: dict[str, Conversation] = {}
        for m in arrivals:
            root = uf.find(m.key)
            conversation = found.setdefault(root, Conversation(root))
            conversation.arrivals.append(m)
            if m.thread_id and m.thread_id.startswith("rf:"):
                conversation.held.add(m.thread_id)
        for name, ids in touching.items():
            conversation = found.get(uf.find(name))
            if conversation:
                conversation.held.update(ids)
        return list(found.values())

    def _gmail_ids(self, conversation: Conversation) -> set[str]:
        return ({self._gmail_id(m.thrid) for m in conversation.arrivals if m.thrid}
                | {tid for tid in conversation.held if tid.startswith("gm:")})

    def contested(self, conversations: Iterable[Conversation]) -> set[str]:
        """The Gmail threads whose copies `place` must see, since it has to choose between them."""
        return {tid for conversation in conversations if len(self._gmail_ids(conversation)) > 1
                for tid in conversation.held if tid.startswith("gm:")}

    def _survivor(self, conversation: Conversation, members: Iterable[Copy] = ()) -> str:
        gmail = self._gmail_ids(conversation)
        if len(gmail) > 1:                            # the later one, as a whole recompute of them would say
            copies = [*conversation.arrivals, *(m for m in members if m.thread_id in conversation.held)]
            thrids = list(dict.fromkeys(m.thrid for m in _in_store_order(copies) if m.thrid))
            return self._gmail_id(thrids[-1]) if thrids else min(gmail)
        if gmail:
            return gmail.pop()
        if conversation.held:
            return min(conversation.held)             # reuse the sticky id (deterministic if a merge joined two)
        return self._minted(conversation.root)        # brand-new: hash its root once

    def place(self, conversations: Iterable[Conversation], members: Iterable[Copy] = ()) -> Placement:
        """Where each conversation's arrivals go, and which ids that absorbs: every copy holding one takes the survivor.
        `members`: the copies of the threads `contested` named."""
        members = list(members)
        given: dict[int, str] = {}
        merged: dict[str, str] = {}
        for conversation in conversations:
            survivor = self._survivor(conversation, members)
            given.update((m.pk, survivor) for m in conversation.arrivals)
            merged.update((tid, survivor) for tid in conversation.held if tid != survivor)
        return Placement(given, merged)

    def regroup(self, copies: Iterable[Copy]) -> Placement:
        """Every copy the account holds, regrouped from what each names. It trusts no id it finds but an rf: one, so a
        gm: id whose every Gmail copy has gone gives way. An id absorbed here is one no copy holds any more: one a
        split left on some copies still has them, and isn't in `merged`."""
        rows = _in_store_order(copies)
        given: dict[int, str] = {}
        for conversation in self.conversations(rows, {}):
            survivor = self._survivor(conversation)
            given.update((m.pk, survivor) for m in conversation.arrivals)
        held = set(given.values())
        merged: dict[str, str] = {}
        for m in rows:
            if m.thread_id and m.thread_id not in held:
                merged.setdefault(m.thread_id, given[m.pk])
        return Placement(given, merged)
