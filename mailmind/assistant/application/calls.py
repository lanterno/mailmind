"""Every AI call in the app goes through call_claude_json or call_claude here, which ask the Claude API through the
gateway. With no API key AI is off: those calls raise AIOff before anything is sent, and status() says "none" rather than
counting them as failed calls. The cost governor may hold a call back instead (HeldBack: OverLimit for one someone asked
for at the daily limit, Deferred for background work), which isn't a failed call either.
"""
import json
import os
import re
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, Concatenate

from mailmind.assistant.application.gateway import DayCost, Gateway
from mailmind.assistant.application.governor import CostGovernor
from mailmind.assistant.application.ports import BatchStatus, NotSent
from mailmind.assistant.domain import models
from mailmind.kernel.parsed import is_object
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
    return s[:140] or type(e).__name__


def _loose_json(out: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", out, re.S)
    return json.loads(m.group(0)) if m else {}


def object_in(out: str) -> dict[str, Any]:
    """The JSON object a reply holds, or {} when it holds none."""
    try:
        obj = json.loads(out)
    except ValueError:
        obj = None
    if is_object(obj):
        return obj
    try:
        return _loose_json(out)
    except Exception:
        return {}


def parse(out: str) -> tuple[str, int, str, str]:
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        raise ValueError("no JSON")
    obj = json.loads(m.group(0))
    imp = int(obj.get("importance", 2))
    return (str(obj.get("summary", ""))[:600], max(1, min(5, imp)),
            str(obj.get("category", "other"))[:32], str(obj.get("action", ""))[:200])


def _noted[**P, R](fn: Callable[Concatenate[Calls, P], R]) -> Callable[Concatenate[Calls, P], R]:
    """Record each call's outcome for status(), passing its result or exception through unchanged.
    A call that was never made (AI off, or held back by the cost governor) isn't recorded."""
    @wraps(fn)
    def run(self: Calls, *a: P.args, **kw: P.kwargs) -> R:
        try:
            out = fn(self, *a, **kw)
        except NotSent:
            raise
        except Exception as e:
            self.note(False, _plain(e))
            raise
        self.note(True, "")
        return out
    return run


class Calls:
    MODEL = os.environ.get("MAILMIND_MODEL")  # optional, e.g. claude-haiku-5-5

    def __init__(self, gateway: Callable[[], Gateway], key: Callable[[], tuple[str, str]],
                 transport: Callable[[], str], governor: Callable[[], CostGovernor],
                 batch_status: Callable[[], BatchStatus]):
        self._gateway = gateway
        self._key = key
        self._transport = transport
        self._governor = governor
        self._batch_status = batch_status
        self._last: dict[str, Any] = {"ok": None, "ts": 0, "error": ""}   # how the most recent AI call went, for status()

    def note(self, ok: bool, error: str) -> None:
        self._last.update(ok=ok, ts=int(time.time()), error=error)

    @_noted
    def noting[T](self, make: Callable[[], T]) -> T:
        """Make a call the way these do, its outcome noted for status()."""
        return make()

    def engine(self) -> str:
        """"api" when an API key resolves, else "none": AI is off."""
        return "api" if self._gateway().on else "none"

    def describe_engine(self) -> str:
        """One line for the startup log."""
        return f"Claude API through {self._transport()} · {self.model_label()} · {self._key_note()}"

    def _key_note(self) -> str:
        """Where the API key came from, or why there is none."""
        key, source = self._key()
        if key:
            return "key from " + source
        if source == "mailmind.config.json":
            return "no key, so AI is off: the anthropic_api_key in mailmind.config.json can't be read"
        return "no key, so AI is off: set anthropic_api_key in mailmind.config.json, or ANTHROPIC_API_KEY"

    def model_label(self) -> str:
        """The model name stored beside each AI result and shown under it in the UI."""
        return self.MODEL or models.DEFAULT

    def status(self) -> dict[str, Any]:
        """What the UI says about AI, with no call made: the engine ("api", or "none" with no key),
        its model, where the key came from, how the most recent call went (last_ok None until one
        has run), and what today's calls cost so far in micro-dollars (None when the usage ledger
        can't be read)."""
        on = self.engine() == "api"
        return {"engine": "api" if on else "none", "model": self.model_label() if on else "",
                "key_from": self._key()[1] if on else "",
                "last_ok": self._last["ok"], "last_ts": self._last["ts"], "last_error": self._last["error"],
                "spent_today_micros": self.spent_today(), "limit_micros": self._governor().limit_micros(),
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

    def spent_today(self) -> int | None:
        """What today's AI calls cost so far, in micro-dollars, or None when the ledger can't be read."""
        try:
            return self._gateway().spent_today()
        except Exception as e:
            log.warning("the usage ledger can't be read", exc_info=e)
            return None

    def usage(self, days: int = 30) -> list[DayCost]:
        """Each of the last `days` local days, newest first: its AI calls and their cost in
        micro-dollars, in all and by feature (the usage ledger's report)."""
        return self._gateway().usage(days)

    @_noted
    def call_claude(self, prompt: str, feature: str = "other") -> str:
        """Plain text result (a thread's digest). `feature` names the call in the usage ledger."""
        return self._gateway().ask(prompt, feature=feature, model=self.MODEL).text

    @_noted
    def call_claude_json(self, prompt: str, schema: dict[str, Any], effort: str | None = None,
                         feature: str = "other") -> dict[str, Any]:
        """Ask Claude for a JSON object matching `schema`. Returns the dict structured output
        (output_config.format) gives, else best-effort regex-parsed JSON from the text. Raises
        RuntimeError ONLY when no reply came (AIOff, one, when AI is off), so the caller can tell
        retry from fallback; a successful-but-unparseable reply yields {}. `feature` names the call in
        the usage ledger."""
        out = self._gateway().ask(prompt, None if os.environ.get("MAILMIND_NO_SCHEMA") else schema,
                                      feature=feature, model=self.MODEL, effort=effort).text
        return object_in(out)
