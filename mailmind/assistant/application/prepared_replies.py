"""Drafting ahead: an editable reply made ready for important action mail, while the user reads it (dwell kick),
proactively for the top-K reply-worthy messages (background tick), and banked from on-demand generations. NEVER
auto-sends: every path ends in the composer for review.

The durable `draft_ahead` table is the ONLY cache (drafts survive restarts). It is keyed on
(account, 't:<thread_id>'|'m:<message_id>'), natural keys, never pk, and every serve revalidates
fingerprint + TTL + eligibility + thread-anchor, so a stale draft is never surfaced. Single-flight per key; the cost
governor decides when it may run; fingerprint = thread state.
"""
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from mailmind.assistant.application.calls import Calls
from mailmind.assistant.application.gateway import Gateway
from mailmind.assistant.application.ports import Answers, Mail, NotSent, Result
from mailmind.assistant.application.reply_draft import ReplyDrafts
from mailmind.assistant.domain import fingerprint, prepared_reply
from mailmind.kernel.errors import NotFound, Refused
from mailmind.kernel.ids import AccountId, Pk, ThreadRef
from mailmind.kernel.parsed import as_int, required_int
from mailmind.kernel.row import Connection, Row
from mailmind.platform import sse
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger
from mailmind.platform.workers.singleflight import SingleFlight

log = get_logger("ai")
tick_log = get_logger("draft")
_WARM = object()                                      # what warm() finds: a fresh draft is stored


class PreparedReplies:
    DRAFT_TICK_INTERVAL = 180        # one tick cycle per 3 min
    DRAFT_TICK_STARTUP_DELAY = 120   # let the startup sync + summarize backlog settle first
    DRAFT_TOPK = 6                   # maintain warm drafts for at most the 6 best candidates (batch=1 per cycle)
    DRAFT_TOPK_PER_ACCOUNT = 2       # fairness across accounts
    DRAFT_RECENCY_WINDOW = 3 * 86400 # only mail from the last 72h; older mail drafts on demand

    def __init__(self, calls: Calls, gateway: Callable[[], Gateway], answers: Answers, uow: UnitOfWork, mail: Mail,
                 replies: ReplyDrafts, looks_human: Callable[[str], bool]):
        self._calls = calls
        self._gateway = gateway
        self._answers = answers
        self._uow = uow
        self._mail = mail
        self._replies = replies
        self._looks_human = looks_human
        self.DRAFT_FLIGHTS = SingleFlight()   # one generation at a time for each (account, ref_key)

    def _draft_eligible(self, row: Row | None) -> bool:
        return prepared_reply.eligible(row, self._looks_human)

    def _draft_fp(self, c: Connection, account: AccountId, thread_id: ThreadRef | None, pk: Pk) -> str:
        """Thread-state hash. Stable across reconcile/reset_folder (built only from
        message_id/ts/count — never pk); flips when a message arrives, leaves, or is swapped.
        Captured BEFORE generation (load-bearing: the 10-40s TOCTOU window can then only
        cause a regeneration, never a stale serve). body_fetched stays in the standalone form
        (a backfilled body → a better draft) but is excluded from the thread form (body
        backfill would churn-invalidate the whole table)."""
        if thread_id:
            rows = self._mail.thread_stamps(c, account, thread_id)
            if not rows:
                return "t2:gone"
            return fingerprint.of((r["message_id"] for r in rows), max(r["ts"] or 0 for r in rows))
        r = self._mail.message_stamp(c, pk)
        return fingerprint.message(r["message_id"] if r else None, r["body_fetched"] if r else 0)

    def _gen_draft_ahead(self, account: AccountId | None, pk: Pk, source: str = "dwell") -> bool | None:
        """Background generation (single-flight per natural key). Writes the durable cache;
        never sends. Returns True on led-success, None on a NEUTRAL bail (follower joined an
        in-flight generation / message became ineligible / already warm), False ONLY when this
        call led a generation that actually failed — the tick's circuit breaker counts False,
        ignores None (a neutral must neither trip nor reset the consecutive-failure count)."""
        with self._uow.read() as c:
            row = self._mail.get_message(c, pk)
            key = prepared_reply.key_of(row) if row else None
            if not row or key is None or not self._draft_eligible(row):
                return None                               # neutral: nothing to (pre-)generate
            acct = account or row["account"]
            tid = row["thread_id"] or None
            fp = self._draft_fp(c, acct, tid, pk)         # captured BEFORE the 10-40s generation: a mid-gen
            mid = row["message_id"]                       # thread change → fp mismatch on read → regen, never a stale serve
            message_key = row["message_key"]

        def warm() -> object | None:                      # a draft for the thread as it is now
            with self._uow.read() as c:
                da = self._answers.draft_ahead_get(c, key[0], key[1])
            return _WARM if prepared_reply.servable(da, fp, int(time.time())) else None

        def make() -> bool | None:
            ok = False
            try:
                res = self._replies.draft(acct, thread_id=tid, pk=(None if tid else pk), me=acct, feature="draft-ahead")
                now = int(time.time())
                with self._uow.write() as c:
                    self._answers.draft_ahead_put(c, key[0], key[1], fp, res["body"], res.get("tone"),
                                                  res.get("model"), mid, source, now)
                    self._answers.draft_ahead_gc(c, now, orphans=False)   # opportunistic hygiene (NOSYNC has no tick);
                                                                  # no orphan sweeps — a reset_folder reingest may be mid-flight
                ok = True
                try:
                    sse.publish("draft-ready", {"account": acct, "pk": int(pk), "key": message_key})
                except Exception:
                    pass
            except NotSent:
                return None                                   # neutral: AI is off, or the governor said not now
            except Exception as e:
                log.warning("draft-ahead failed", exc_info=e)
                try:
                    with self._uow.write() as c:
                        self._answers.draft_ahead_fail(c, key[0], key[1], int(time.time()))
                except Exception:
                    pass
            return ok

        # The lead re-checks warm() first: a generation that stored its draft between our read above and
        # our taking the lead must not be paid for twice.
        led = self.DRAFT_FLIGHTS.run(key, make, warm, wait=45)
        return led if isinstance(led, bool) else None     # neutral: already warm, or joined another's run

    def get(self, qs: Mapping[str, Sequence[str]], kick_ok: bool = False) -> Result:
        """GET /api/ai/draft-ahead?pk=[&peek=1] → cached draft (ready) / kicks generation
        (generating) / none. peek=1 is strictly read-only (reports inflight, never kicks).
        kick_ok=False also behaves like peek for the SPAWN decision — the kick spends a real
        claude call, so it demands the CSRF token (a cross-site <img> flood must not be able
        to burn generations). Never generates synchronously, never sends; fingerprint + TTL +
        eligibility + thread-anchor are revalidated on EVERY serve so a stale draft is never
        surfaced."""
        asked = as_int((qs.get("pk") or ["0"])[0])
        if asked is None:
            raise Refused("bad pk")
        pk = Pk(asked)
        peek = (qs.get("peek") or ["0"])[0] == "1" or not kick_ok
        if not pk:
            return {"ok": True, "status": "none"}
        with self._uow.read() as c:
            row = self._mail.get_message(c, pk)
            if not row:
                return {"ok": True, "status": "none"}
            acct = row["account"]
            tid = row["thread_id"] or None
            if not prepared_reply.for_the_anchor(pk, tid, self._mail.thread_anchor_pk(c, acct, tid) if tid else None):
                return {"ok": True, "status": "none"}
            if not self._draft_eligible(row):
                return {"ok": True, "status": "none"}
            key = prepared_reply.key_of(row)
            if key is None:
                return {"ok": True, "status": "none"}
            fp = self._draft_fp(c, acct, tid, pk)
            da = self._answers.draft_ahead_get(c, key[0], key[1])
            now = int(time.time())
            if da is None or not prepared_reply.servable(da, fp, now):
                if self.DRAFT_FLIGHTS.busy(key):
                    return {"ok": True, "status": "generating"}
                if peek:
                    return {"ok": True, "status": "none"}
                if prepared_reply.failed_lately(da, now):
                    return {"ok": True, "status": "none"}   # recently failed → back off (client falls to on-demand)
                if not self._answers.reply_worthy(c, pk):
                    return {"ok": True, "status": "none"}
                threading.Thread(target=self._gen_draft_ahead, args=(acct, pk), daemon=True).start()
                return {"ok": True, "status": "generating"}
        with self._uow.write() as c:                          # only a draft served is touched: asking takes no lock
            self._answers.draft_ahead_touch(c, key[0], key[1], now)
        return {"ok": True, "status": "ready", "body": da["body"], "tone": da["tone"]}

    def _pick_draft_candidate(self) -> Row | None:
        """One tick's worth of work: the single best reply-worthy message that isn't already
        warm/inflight/backing-off (batch=1). Pure SQL + Python — no AI cost. Steady state
        finds nothing (fp dedup) and exits in ~1ms."""
        now = int(time.time())
        with self._uow.read() as c:
            rows = self._mail.draft_candidates(c, now - self.DRAFT_RECENCY_WINDOW)
            picked: Row | None = None
            seen: set[tuple[str, str]] = set()
            warm_total = 0
            warm_acct: dict[str, int] = {}
            for r in rows:
                if warm_total >= self.DRAFT_TOPK:
                    break                                            # warm set is full — nothing to do
                if not self._draft_eligible(r):
                    continue                                         # human-sender check (Python-only)
                key = prepared_reply.key_of(r)
                if not key or key in seen:
                    continue
                seen.add(key)
                da = self._answers.draft_ahead_get(c, key[0], key[1])
                fp = self._draft_fp(c, r["account"], r["thread_id"] or None, r["pk"])
                if prepared_reply.servable(da, fp, now):
                    warm_total += 1
                    warm_acct[r["account"]] = warm_acct.get(r["account"], 0) + 1
                    continue                                         # already warm — the restart-storm killer
                if warm_acct.get(r["account"], 0) >= self.DRAFT_TOPK_PER_ACCOUNT:
                    continue                                         # per-account fairness
                if prepared_reply.backing_off(da, now):
                    continue                                         # persisted exponential backoff
                if self.DRAFT_FLIGHTS.busy(key):
                    continue
                if picked is None:
                    picked = r
                    warm_total += 1
                    warm_acct[r["account"]] = warm_acct.get(r["account"], 0) + 1
            return picked

    def rounds(self) -> Callable[[], None]:
        """A pre-drafter's round function, with its own failure count and circuit breaker. Each round
        drafts at most one reply ahead, only when the cost governor would let it go (nobody is using
        AI, someone was here lately, the day's limit isn't reached, the API isn't rejecting it), then
        collects the expired drafts."""
        breaker = prepared_reply.Breaker()

        def draft_ahead_round() -> None:
            if breaker.is_open(time.time()):
                pass                                                 # circuit breaker open (an outage)
            elif self._gateway().held_back("draft-ahead", self._calls.MODEL):
                pass                                                 # yielding, away, at the limit, rejected
            else:
                cand = self._pick_draft_candidate()
                if cand is not None:
                    ok = self._gen_draft_ahead(cand["account"], cand["pk"], source="topk")
                    if breaker.heard(ok, time.time()):
                        tick_log.warning("tick suspended 30m after repeated failures")
                with self._uow.write() as c:
                    self._answers.draft_ahead_gc(c, int(time.time()))
        return draft_ahead_round

    def reply(self, body: Mapping[str, Any]) -> Result:
        """Draft an editable reply to a thread/message via claude."""
        account = body.get("account")
        thread_id = body.get("thread_id")
        pk = None if body.get("pk") is None else Pk(required_int(body["pk"]))
        tone = str(body.get("tone") or "balanced")[:16]
        instruction = str(body.get("instruction") or "")[:400]
        default_shape = prepared_reply.default_shaped(instruction, tone)
        row: Row | None = None
        fp: str | None = None
        key: tuple[AccountId, str] | None = None
        if pk is not None:
            with self._uow.read() as c:
                row = self._mail.get_message(c, pk)
                if not row:
                    raise NotFound("not found")
                key = prepared_reply.key_of(row)
                # fp captured BEFORE generation (same load-bearing rule as _gen_draft_ahead):
                # a mid-generation thread change must mismatch on read → regenerate, never stale-serve.
                fp = self._draft_fp(c, row["account"], row["thread_id"] or None, row["pk"])
                da = self._answers.draft_ahead_get(c, key[0], key[1]) if default_shape and key is not None else None
            # A fp-fresh banked draft answers a default-shaped request for free — the pre-gen
            # paths already paid for it (and this covers SSE-outage strandings).
            if key is not None and da and prepared_reply.servable(da, fp, int(time.time())):
                with self._uow.write() as c:
                    self._answers.draft_ahead_touch(c, key[0], key[1], int(time.time()))
                return {"ok": True, "body": da["body"], "tone": da["tone"] or tone,
                        "model": da["model"] or "cache"}
            account = account or row["account"]
            thread_id = thread_id or row["thread_id"]      # prefer the whole thread for context
            if not row["thread_id"]:
                pk = row["pk"]                              # standalone message: draft from it alone
            else:
                pk = None
        if not account or account not in {a["email"] for a in self._mail.accounts()}:
            raise Refused()
        # The mail is in its own account's rows. `account` is who's replying: From may name another.
        res = self._replies.draft(row["account"] if row is not None else account, thread_id=thread_id, pk=pk,
                                  instruction=instruction, tone=tone, me=account)
        # Bank a default-shaped success into the draft-ahead cache (the user already paid for
        # this call; a closed-and-reopened composer or same-thread revisit is then free).
        # Custom instruction / non-default tone are personal one-offs — never banked.
        if default_shape and row is not None and key is not None and fp is not None:
            try:
                with self._uow.write() as c:
                    self._answers.draft_ahead_put(c, key[0], key[1], fp, res["body"], res.get("tone"),
                                          res.get("model"), row["message_id"], "demand", int(time.time()))
            except Exception:
                pass                                        # banking is best-effort; the reply itself already succeeded
        return res
