"""On-demand AI summary of an email thread (or single message) via Claude — reuses
Calls.call_claude (the Claude API). Cached in thread_summaries
(content-fingerprinted, so a new reply invalidates it). Single-flight per
(account,thread_id,scope) + a global concurrency cap so double-clicks never pile up Claude calls. Untrusted email is bounded and
the system prompt tells the model to treat it as data, not instructions.
"""
from datetime import datetime, timezone
import json
from typing import Any

from mailmind.assistant.application.calls import Calls
from mailmind.assistant.application.context import ContextAssembler
from mailmind.assistant.application.ports import Answers, NotSent, Result
from mailmind.assistant.domain import digest, fingerprint, prompts
from mailmind.assistant.domain.digest import Digest
from mailmind.kernel.errors import NotFound, Unreachable
from mailmind.kernel.ids import AccountId, ThreadRef
from mailmind.kernel.row import Row
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger
from mailmind.platform.workers.singleflight import SingleFlight

log = get_logger("ai")


class ThreadDigest:
    def __init__(self, calls: Calls, answers: Answers, uow: UnitOfWork, context: ContextAssembler):
        self._calls = calls
        self._answers = answers
        self._uow = uow
        self._context = context
        self._FLIGHTS = SingleFlight()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _fingerprint(self, rows: list[Row]) -> str:
        """The thread as summarized: its messages' Message-IDs and the newest one's time, so a
        renumbered folder leaves its summary fresh."""
        return fingerprint.of((r["message_id"] for r in rows), max(r["ts"] or 0 for r in rows))

    def _expand(self, row: Row) -> dict[str, Any]:
        def _j(s: str | None) -> list[Any]:
            try:
                return json.loads(s) if s else []
            except Exception:
                return []
        return {"tldr": row["tldr"], "points": _j(row["points"]), "action": row["action"],
                "participants": _j(row["participants"]), "message_count": row["message_count"],
                "model": row["model"], "created_at": row["created_at"]}

    def summarize(self, account: AccountId, thread_id: ThreadRef, scope: str = "thread", force: bool = False) -> Result | None:
        """The thread's summary, or None when another request is still writing it past the wait."""
        with self._uow.read() as c:
            rows = self._context.rows(c, account, thread_id)
        if not rows:
            raise NotFound("nothing to summarize")
        fp = self._fingerprint(rows)
        with self._uow.read() as c:
            cached = self._answers.get_thread_summary(c, account, thread_id, scope)
        if cached and not force:
            return {"ok": True, "account": account, "thread_id": thread_id, "scope": scope,
                    "cached": True, "stale": cached["fingerprint"] != fp, **self._expand(cached)}

        def read() -> Result | None:                    # a summary of the thread as it is now
            with self._uow.read() as c:
                row = self._answers.get_thread_summary(c, account, thread_id, scope)
            if row is None or row["fingerprint"] != fp:
                return None
            return {"ok": True, "account": account, "thread_id": thread_id, "scope": scope,
                    "cached": True, "stale": False, **self._expand(row)}

        def make() -> Result:
            blocks, omitted = self._context.gather(rows)
            subject = rows[0]["subject"] or "(no subject)"
            kind = "MESSAGE" if scope == "message" else "THREAD"
            prompt = prompts.THREAD.fill(kind=kind, label=prompts.THREAD_LABELS[kind], subject=subject, n=len(rows),
                                         omit=(prompts.THREAD_OMITTED.replace("{n}", str(omitted)) if omitted else ""),
                                         blocks="\n".join(blocks))
            try:
                out = self._calls.call_claude(prompt, feature="thread")
            except NotSent:                         # AI off, or over the daily limit: the route answers it
                raise
            except Exception as e:                      # claude missing / timeout / rate-limit: retryable, no cache
                log.warning("claude failed", exc_info=e)
                raise Unreachable("summary unavailable", retry=True)
            parsed: Digest | None = digest.parse(out)
            model = self._calls.model_label()
            if parsed is None:                          # replied but not JSON: cache a safe fallback, don't loop
                parsed = {"tldr": subject, "points": [(rows[-1]["snippet"] or "")[:200]],
                          "action": "", "participants": []}
                model = "parse-error"
            if not parsed["participants"]:
                parsed["participants"] = sorted({r["from_name"] or r["from_addr"] for r in rows if r["from_addr"]})[:12]
            row: dict[str, Any] = {"account": account, "thread_id": thread_id, "scope": scope, "fingerprint": fp,
                   "tldr": parsed["tldr"], "points": json.dumps(parsed["points"]),
                   "action": parsed["action"], "participants": json.dumps(parsed["participants"]),
                   "message_count": len(rows), "model": model, "created_at": self._now()}
            with self._uow.write() as c:
                self._answers.save_thread_summary(c, row)
            return {"ok": True, "account": account, "thread_id": thread_id, "scope": scope,
                    "cached": False, "stale": False, "tldr": parsed["tldr"], "points": parsed["points"],
                    "action": parsed["action"], "participants": parsed["participants"],
                    "message_count": len(rows), "model": model, "created_at": row["created_at"]}

        return self._FLIGHTS.run((account, thread_id, scope), make, read, wait=60, recheck=not force)
