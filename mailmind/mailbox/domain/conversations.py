"""Conservative, account-scoped conversation threading for MailMind (stdlib only).

assign gives each message of an account its thread id, in precedence order:
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
from collections.abc import Sequence

from mailmind.kernel.row import Row

_ID = re.compile(r"<[^<>\s]+>")
_WELL = re.compile(r"^<[^<>\s]+@[^<>\s]+>$")


def _ids(blob: str | None) -> list[str]:
    return _ID.findall(blob or "")


class _UF:
    """Disjoint-set with a deterministic (lexicographically-smaller) root."""
    def __init__(self):
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


def assign(rows: Sequence[Row], account: str) -> tuple[dict[int, str], dict[str, str]]:
    """`rows`: the account's messages, each with pk, message_id, gmail_thread, in_reply_to, refs and the
    thread_id it holds. Returns ({pk: its thread id now}, {an id no row holds now: the id its rows took}):
    one a split left on some rows still has them, and isn't in the second."""
    uf = _UF()
    self_key: dict[int, str] = {}
    # Tier 2 — union each row's self-id with its In-Reply-To / References parents.
    for r in rows:
        pk = r["pk"]
        mid = (r["message_id"] or "").strip()
        key = mid if _WELL.match(mid) else f"pk:{pk}"   # garbage/blank id -> own singleton
        self_key[pk] = key
        uf.union(key, key)
        for p in _ids(r["in_reply_to"]) + _ids(r["refs"]):
            uf.union(key, p)
    # Tier 1 — union all rows sharing a Gmail thrid (authoritative, same account).
    thrid_groups: dict[str, list[int]] = {}
    for r in rows:
        g = (r["gmail_thread"] or "").strip()
        if g:
            thrid_groups.setdefault(g, []).append(r["pk"])
    for pks in thrid_groups.values():
        for pk in pks[1:]:
            uf.union(self_key[pks[0]], self_key[pk])
    # a component backed by a Gmail thrid gets the authoritative gm: label
    root_thrid: dict[str, str] = {}
    for g, pks in thrid_groups.items():
        root_thrid[uf.find(self_key[pks[0]])] = g
    # STICKY thread_id: once a component has an rf: id, keep it as the thread grows (the union-find
    # root is the lexicographically-smallest member id, which a new reply CAN change — so we must
    # NOT re-derive it each time). Only a brand-new component with no prior id mints a fresh hash.
    comp_existing: dict[str, set[str]] = {}
    for r in rows:
        root = uf.find(self_key[r["pk"]])
        ex = (r["thread_id"] or "")
        if ex.startswith("rf:"):
            comp_existing.setdefault(root, set()).add(ex)
    given: dict[int, str] = {}                # pk -> its id now
    held: set[str] = set()                    # every id a row holds now
    for r in rows:
        root = uf.find(self_key[r["pk"]])
        if root in root_thrid:
            tid = f"gm:{account}:{root_thrid[root]}"
        elif comp_existing.get(root):
            tid = min(comp_existing[root])    # reuse the sticky id (deterministic if a merge joined two)
        else:                                 # brand-new component: hash its root once
            h = hashlib.sha1(root.encode("utf-8", "replace")).hexdigest()[:16]
            tid = f"rf:{account}:{h}"
        given[r["pk"]] = tid
        held.add(tid)
    moved: dict[str, str] = {}
    for r in rows:
        old = r["thread_id"] or ""
        if old and old not in held:
            moved.setdefault(old, given[r["pk"]])
    return given, moved
