"""Conservative, account-scoped conversation threading for MailMind.

A Threader gives each message of an account its thread id, in precedence order:
  1) Gmail X-GM-THRID  — same account + same thrid = one thread (most reliable),
  2) a union-find over the Message-ID / In-Reply-To / References graph (JWZ-lite),
  3) the message's own id (singleton).
It NEVER threads on subject, NEVER spans accounts, and gives every row a non-NULL
thread_id. The rf: thread_id is derived from the component's union-find ROOT (the
lexicographically-smallest id in it, the ids its rows only refer to included), NOT the whole
member set, so a thread keeps the SAME id as replies arrive, and mints the same one again when
it is synced whole after every copy went: its root is the same whichever rows were there first.

Two ways in, and for mail that only arrives they end with the same threads:
  `conversations` and `place` take what just arrived and the ids of the threads it touches, which
  is all a sync has to read: everything a thread holds is one thread already.
  `regroup` takes every copy the account holds. It is the repair.
They give the same ids too, but for a thread holding two Gmail threads: `regroup` decides between
them whenever it runs, and `place` only when a new Gmail id comes to it. And mail that goes, or a copy
stored again naming something else, can leave a thread unconnected: `place` keeps it one, where `regroup`
parts what no longer reaches a Gmail copy at once, and lets the rest part at their next merge.

An id is sticky once given, and goes only when a merge absorbs it (two threads joined, or a thread
that gained a Gmail thread id). The survivor is a Gmail id over an rf: id, the smallest of several
rf: ids, and of two Gmail threads joined by headers the one that comes later in store order. Whoever
keeps state under the absorbed id moves it in the same transaction (events.ConversationsMerged).

The Rust core decides all of it (`mm_mailbox::threading`), as every client does, so an id minted here is the one
minted there. This module keeps the types the app passes around and hands Rust plain tuples: a copy as
`(pk, message_id, gmail_thread, in_reply_to, refs, thread_id, folder, ts)`.
"""
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field

from mailmind_leaves import (
    THREAD_REFERENCES_MAX,
    thread_contested,
    thread_conversations,
    thread_key,
    thread_names,
    thread_place,
    thread_references,
    thread_regroup,
    thread_thrid,
)

REFERENCES_MAX = THREAD_REFERENCES_MAX   # how many of a References header count, nearest first: a forged one can be as long as it likes


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
        return thread_thrid(self.gmail_thread)

    @property
    def key(self) -> str:
        """Its own id, or a garbage or blank one's stand-in, which no other copy can name."""
        return thread_key(self.pk, self.message_id)

    def references(self) -> list[str]:
        """The ids it says it follows."""
        return thread_references(self.in_reply_to, self.refs)

    def names(self) -> set[str]:
        """Everything another copy can meet it by: two copies that share a name are one thread."""
        return set(thread_names(self.message_id, self.gmail_thread, self.in_reply_to, self.refs))


type _Plain = tuple[int, str | None, str | None, str | None, str | None, str | None, str, int | None]


def _plain(m: Copy) -> _Plain:
    return (m.pk, m.message_id, m.gmail_thread, m.in_reply_to, m.refs, m.thread_id, m.folder, m.ts)


@dataclass(slots=True)
class Conversation:
    """Copies placed together: `arrivals`, and `held`, the ids of the threads they touch."""
    root: str
    arrivals: list[Copy] = field(default_factory=lambda: list[Copy]())
    held: set[str] = field(default_factory=lambda: set[str]())


def _crossing(conversations: Iterable[Conversation]) -> list[tuple[str, list[_Plain], list[str]]]:
    """Each conversation as Rust reads it. `held` goes in the set's own order, which is the order `merged` takes."""
    return [(c.root, [_plain(m) for m in c.arrivals], list(c.held)) for c in conversations]


@dataclass(frozen=True, slots=True)
class Placement:
    """`given`: each copy's thread id now, by pk. `merged`: each id absorbed, and the id that absorbed it."""
    given: dict[int, str]
    merged: dict[str, str]


class Threader:
    """Decides the thread ids of one account's mail, and holds no state of its own."""

    def __init__(self, account: str) -> None:
        self._account = account

    def conversations(self, arrivals: Iterable[Copy], touching: Mapping[str, Collection[str]]) -> list[Conversation]:
        """Copies that belong together, with the threads they touch: `touching` says, for each name the arrivals
        name, the ids of the threads that hold a copy naming it. Two threads touched by one conversation are one
        thread from now on. An arrival's own id counts as touched, so one whose references changed stays in its
        thread, with or without a Message-ID for its links to find it by, and so does its Gmail thread's. Arrivals
        that touch one thread are one conversation, so `place` never absorbs an id it gives, nor one into an id it
        absorbs too."""
        arrivals = list(arrivals)
        found = thread_conversations(self._account, [_plain(m) for m in arrivals],
                                     [(name, list(ids)) for name, ids in touching.items()])
        return [Conversation(root, [arrivals[at] for at in positions], set(held)) for root, positions, held in found]

    def contested(self, conversations: Iterable[Conversation]) -> set[str]:
        """The Gmail threads whose copies `place` must see, since it has to choose between them."""
        return set(thread_contested(self._account, _crossing(conversations)))

    def place(self, conversations: Iterable[Conversation], members: Iterable[Copy] = ()) -> Placement:
        """Where each conversation's arrivals go, and which ids that absorbs: every copy holding one takes the survivor.
        `members`: the copies of the threads `contested` named."""
        given, merged = thread_place(self._account, _crossing(conversations), [_plain(m) for m in members])
        return Placement(dict(given), dict(merged))

    def regroup(self, copies: Iterable[Copy]) -> Placement:
        """Every copy the account holds, regrouped from what each names. It trusts no id it finds but an rf: one, so a
        gm: id whose every Gmail copy has gone gives way. An id absorbed here is one no copy holds any more: one a
        split left on some copies still has them, and isn't in `merged`."""
        given, merged = thread_regroup(self._account, [_plain(m) for m in copies])
        return Placement(dict(given), dict(merged))
