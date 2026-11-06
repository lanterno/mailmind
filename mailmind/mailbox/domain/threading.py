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

An id is sticky once given. When a thread's rows take another id (two threads merged, or a
thread that gained a Gmail thread id), the state kept under the id no row holds any more moves
to the id its rows hold now, in the same transaction: mutes, follow-ups and Reply Later, thread
summaries and the reply drafted ahead.
"""
import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass

_ID = re.compile(r"<[^<>\s]+>")
_WELL = re.compile(r"^<[^<>\s]+@[^<>\s]+>$")


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
        return _ids(self.in_reply_to) + _ids(self.refs)


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


@dataclass(frozen=True, slots=True)
class Placement:
    """`given`: each copy's thread id now, by pk. `merged`: each id that no copy holds any more, and the id its
    copies took: one a split left on some copies still has them, and isn't here."""
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

    def regroup(self, copies: Iterable[Copy]) -> Placement:
        """Every copy the account holds, regrouped from what each names."""
        rows = _in_store_order(copies)
        uf = _UF()
        # Tier 2 — union each row's self-id with its In-Reply-To / References parents.
        for m in rows:
            uf.union(m.key, m.key)
            for p in m.references():
                uf.union(m.key, p)
        # Tier 1 — union all rows sharing a Gmail thrid (authoritative, same account).
        thrid_groups: dict[str, list[Copy]] = {}
        for m in rows:
            if m.thrid:
                thrid_groups.setdefault(m.thrid, []).append(m)
        for members in thrid_groups.values():
            for m in members[1:]:
                uf.union(members[0].key, m.key)
        # a component backed by a Gmail thrid gets the authoritative gm: label
        root_thrid = {uf.find(members[0].key): g for g, members in thrid_groups.items()}
        # STICKY thread_id: once a component has an rf: id, keep it as the thread grows (the union-find
        # root is the lexicographically-smallest member id, which a new reply CAN change — so we must
        # NOT re-derive it each time). Only a brand-new component with no prior id mints a fresh hash.
        comp_existing: dict[str, set[str]] = {}
        for m in rows:
            if (m.thread_id or "").startswith("rf:"):
                comp_existing.setdefault(uf.find(m.key), set()).add(m.thread_id or "")
        given: dict[int, str] = {}
        for m in rows:
            root = uf.find(m.key)
            if root in root_thrid:
                given[m.pk] = self._gmail_id(root_thrid[root])
            elif comp_existing.get(root):
                given[m.pk] = min(comp_existing[root])      # reuse the sticky id (deterministic if a merge joined two)
            else:                                           # brand-new component: hash its root once
                given[m.pk] = self._minted(root)
        held = set(given.values())
        merged: dict[str, str] = {}
        for m in rows:
            old = m.thread_id or ""
            if old and old not in held:
                merged.setdefault(old, given[m.pk])
        return Placement(given, merged)
