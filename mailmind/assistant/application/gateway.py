"""The gateway every AI call goes through: it builds the request for the model in use, from what
the domain knows about that model, asks the cost governor whether it may go now, sends it through
the LanguageModel bootstrap bound, and adds each call to the usage ledger: what it cost, under the
feature that made it."""
import contextlib
import time
from collections.abc import Callable
from datetime import date, timedelta

from mailmind.assistant.application.governor import CostGovernor
from mailmind.assistant.application.ports import (LanguageModel, ModelError, NotSent, Prompt, Reply,
                                                  Spend, Usage, UsageLedger)
from mailmind.assistant.domain import features, models, pricing
from mailmind.assistant.domain.schema import structured

MAX_TOKENS = 16000                 # a ceiling, not a target; adaptive thinking counts against it


def local_day(ts: float) -> str:
    """The local date a moment counts against, YYYY-MM-DD: spending rolls at local midnight."""
    return date.fromtimestamp(ts).isoformat()


class Gateway:
    def __init__(self, model: Callable[[], LanguageModel], ledger: UsageLedger | None = None,
                 clock: Callable[[], float] = time.time, governor: CostGovernor | None = None):
        self._model = model        # called at each use: the model for the key in force now
        self._ledger = ledger
        self._clock = clock
        self._governor = governor

    @property
    def on(self) -> bool:
        """False when AI is off: there is no key, and every call raises AIOff unsent."""
        return self._model().enabled

    def ask(self, text: str, schema: dict | None = None, *, feature: str, model: str | None = None,
            effort: str | None = None) -> Reply:
        """Send `text` as one user turn to `model` (the default one unless named), with structured
        output when `schema` is given, at `effort` (medium unless named) where the model takes
        one, and the refusal fallback where it takes that. Raises ModelError when no reply came,
        and one of NotSent's when no call was made: AIOff, or what the cost governor raises when
        it holds the call back (OverLimit, Deferred). The call is counted under `feature`
        whatever its outcome, if it was made."""
        name = model or models.DEFAULT
        prompt = Prompt(text=text, model=name, max_tokens=MAX_TOKENS,
                        schema=None if schema is None else structured(schema),
                        effort=(effort or models.EFFORT) if models.takes_effort(name) else None,
                        fallback=models.takes_fallback(name))
        lm = self._model()
        if not lm.enabled:
            return lm.complete(prompt)          # raises AIOff: nothing is sent
        with self._admitted(feature, name, lm):
            try:
                reply = lm.complete(prompt)
            except NotSent:
                raise
            except ModelError as e:
                self._heard(feature, name, lm, e)
                self._count(feature, name, "", "rejected" if e.permanent else "failed", Usage())
                raise
        self._heard(feature, name, lm, None)
        self._count(feature, name, reply.model, "refused" if reply.refused else "ok", reply.usage)
        return reply

    def held_back(self, feature: str, model: str | None = None) -> str | None:
        """Why a call for `feature` would be held back now ("yielding", "away", "limit",
        "breaker"), or None when it would go. Background work asks before a batch."""
        if self._governor is None:
            return None
        return self._governor.held_back(features.kind(feature), feature, model or models.DEFAULT,
                                        self._model())

    def _admitted(self, feature, name, lm):
        if self._governor is None:
            return contextlib.nullcontext()
        return self._governor.call(features.kind(feature), feature, name, lm)

    def _heard(self, feature, name, lm, error):
        if self._governor is not None:
            self._governor.heard(feature, name, lm, error)

    def _count(self, feature: str, asked: str, served: str, outcome: str, usage: Usage) -> None:
        if self._ledger is None:
            return
        now = self._clock()
        cost = pricing.cost_micros(asked, served, usage.input_tokens, usage.output_tokens,
                                   usage.cache_write_tokens, usage.cache_read_tokens)
        try:
            self._ledger.record(Spend(local_day(now), feature, asked, served, outcome, usage, cost,
                                      int(now)))
        except Exception as e:     # the call itself went fine; a ledger write never undoes it
            print(f"[ai] the usage ledger failed: {type(e).__name__}", flush=True)

    def spent_today(self) -> int:
        """What today's calls cost so far, in micro-dollars."""
        return self._ledger.spent(local_day(self._clock())) if self._ledger else 0

    def usage(self, days: int = 30) -> list[dict]:
        """Each of the last `days` local days, newest first: its calls and cost, in micro-dollars,
        in all and by feature. A day without calls is there with zeros."""
        today = date.fromtimestamp(self._clock())
        wanted = [(today - timedelta(days=i)).isoformat() for i in range(max(1, days))]
        out = {d: {"day": d, "calls": 0, "cost_micros": 0, "features": {}} for d in wanted}
        for row in (self._ledger.days(wanted[-1]) if self._ledger else []):
            day = out.get(row["day"])
            if day is None:
                continue
            day["calls"] += row["calls"]
            day["cost_micros"] += row["cost_micros"]
            day["features"][row["feature"]] = {"calls": row["calls"], "cost_micros": row["cost_micros"]}
        return [out[d] for d in wanted]
