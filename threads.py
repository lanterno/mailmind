"""Conservative, account-scoped conversation threading for MailMind (stdlib only).

recompute_threads(c, account) assigns messages.thread_id, in precedence order:
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
import re
import hashlib

_ID = re.compile(r"<[^<>\s]+>")
_WELL = re.compile(r"^<[^<>\s]+@[^<>\s]+>$")


def _ids(blob):
    return _ID.findall(blob or "")


class _UF:
    """Disjoint-set with a deterministic (lexicographically-smaller) root."""
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


def recompute_threads(c, account):
    rows = c.execute(
        "SELECT pk, message_id, gmail_thread, in_reply_to, refs, thread_id "
        "FROM messages WHERE account=?", (account,)).fetchall()
    if not rows:
        return
    uf = _UF()
    self_key = {}
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
    thrid_groups = {}
    for r in rows:
        g = (r["gmail_thread"] or "").strip()
        if g:
            thrid_groups.setdefault(g, []).append(r["pk"])
    for pks in thrid_groups.values():
        for pk in pks[1:]:
            uf.union(self_key[pks[0]], self_key[pk])
    # a component backed by a Gmail thrid gets the authoritative gm: label
    root_thrid = {}
    for g, pks in thrid_groups.items():
        root_thrid[uf.find(self_key[pks[0]])] = g
    # STICKY thread_id: once a component has an rf: id, keep it as the thread grows (the union-find
    # root is the lexicographically-smallest member id, which a new reply CAN change — so we must
    # NOT re-derive it each time). Only a brand-new component with no prior id mints a fresh hash.
    comp_existing = {}
    for r in rows:
        root = uf.find(self_key[r["pk"]])
        ex = (r["thread_id"] or "")
        if ex.startswith("rf:"):
            comp_existing.setdefault(root, set()).add(ex)
    given, held = {}, set()                   # pk -> its id now; every id a row holds now
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
        c.execute("UPDATE messages SET thread_id=? WHERE pk=? AND (thread_id IS NULL OR thread_id<>?)",
                  (tid, r["pk"], tid))
    # An id no row holds any more hands its state to the id its rows took. One a split left on some
    # rows still has them, and keeps its state.
    moved = {}
    for r in rows:
        old = r["thread_id"] or ""
        if old and old not in held:
            moved.setdefault(old, given[r["pk"]])
    for old, surv in moved.items():
        _rekey(c, account, old, surv)


def _rekey(c, account, old, surv):
    """Move the state kept under thread `old` to `surv`. Where `surv` has a row of its own already,
    that row stays and `old`'s goes (UPDATE OR IGNORE, then DELETE)."""
    for tbl in ("muted", "followups", "thread_summaries"):
        try:
            c.execute(f"UPDATE OR IGNORE {tbl} SET thread_id=? WHERE account=? AND thread_id=?",
                      (surv, account, old))
            c.execute(f"DELETE FROM {tbl} WHERE account=? AND thread_id=?", (account, old))
        except Exception:
            pass                              # table may not exist yet on a pre-migration DB
    try:
        c.execute("UPDATE OR IGNORE draft_ahead SET ref_key=? WHERE account=? AND ref_key=?",
                  ("t:" + surv, account, "t:" + old))
        c.execute("DELETE FROM draft_ahead WHERE account=? AND ref_key=?", (account, "t:" + old))
    except Exception:
        pass


