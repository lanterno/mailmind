"""How AI stands, with no call made: what the UI and the startup line say of it, and how the most recent call went. With no
API key the engine is "none", which is no failure. A call the cost governor held back (HeldBack: OverLimit for one someone
asked for at the daily limit, Deferred for background work) wasn't made, so it isn't one either.
"""
import time
from collections.abc import Callable
from typing import Any

from mailmind.assistant.application.gateway import Gateway
from mailmind.assistant.application.governor import CostGovernor
from mailmind.assistant.application.ports import BatchStatus, NotSent
from mailmind.platform.logging import get_logger

log = get_logger("ai")


def _plain(e: Exception) -> str:
    """A failed call's reason in words the setup card can show (never the prompt)."""
    s = str(e)
    if " 401" in s or "authentication" in s.lower() or "invalid x-api-key" in s.lower():
        return "the API key was rejected"
    if " 429" in s or "rate" in s.lower():
        return "rate-limited for now"
    if "timed out" in s.lower():
        return "it timed out"
    status = getattr(e, "status", None)        # never what the API said: it can quote the request back
    return f"the API answered {status}" if status else type(e).__name__


class Status:
    def __init__(self, gateway: Callable[[], Gateway], key: Callable[[], tuple[str, str]],
                 governor: Callable[[], CostGovernor], batch_status: Callable[[], BatchStatus],
                 model_label: Callable[[], str]):
        self._gateway = gateway
        self._key = key
        self._governor = governor
        self._batch_status = batch_status
        self._model_label = model_label
        self._last: dict[str, Any] = {"ok": None, "ts": 0, "error": ""}   # how the most recent AI call went, for report()

    def note(self, ok: bool, error: str) -> None:
        self._last.update(ok=ok, ts=int(time.time()), error=error)

    def noting[T](self, make: Callable[[], T]) -> T:
        """Make a call, recording its outcome for report(), and pass its result or exception through unchanged. A call
        that was never made (AI off, or held back by the cost governor) isn't recorded."""
        try:
            out = make()
        except NotSent:
            raise
        except Exception as e:
            self.note(False, _plain(e))
            raise
        self.note(True, "")
        return out

    def describe_engine(self) -> str:
        """One line for the startup log."""
        return f"Claude API · {self._model_label()} · {self._key_note()}"

    def _key_note(self) -> str:
        """Where the API key came from, or why there is none."""
        key, source = self._key()
        if key:
            return "key from " + source
        if source == "mailmind.config.json":
            return "no key, so AI is off: the anthropic_api_key in mailmind.config.json can't be read"
        return "no key, so AI is off: keep one with `uv run python -m mailmind ai key`, or set ANTHROPIC_API_KEY"

    def report(self) -> dict[str, Any]:
        """What the UI says about AI, with no call made: the engine ("api", or "none" with no key),
        its model, where the key came from, how the most recent call went (last_ok None until one
        has run), and what today's calls cost so far in micro-dollars (None when the usage ledger
        can't be read)."""
        on = self._gateway().on
        return {"engine": "api" if on else "none", "model": self._model_label() if on else "",
                "key_from": self._key()[1] if on else "",
                "last_ok": self._last["ok"], "last_ts": self._last["ts"], "last_error": self._last["error"],
                "spent_today_micros": self._spent_today(), "limit_micros": self._governor().limit_micros(),
                **self._batch_note(on)}

    def _batch_note(self, on: bool) -> dict[str, int]:
        """`batch_waiting`, when there's a batch out to say so of."""
        if not on:
            return {}
        try:
            n = self._batch_status()["waiting"]
        except Exception:
            return {}
        return {"batch_waiting": n} if n else {}

    def _spent_today(self) -> int | None:
        """What today's AI calls cost so far, in micro-dollars, or None when the ledger can't be read."""
        try:
            return self._gateway().spent_today()
        except Exception as e:
            log.warning("the usage ledger can't be read", exc_info=e)
            return None
