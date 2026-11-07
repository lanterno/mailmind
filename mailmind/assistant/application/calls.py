"""Every AI call in the app goes through call_claude_json or call_claude here, which ask the Claude API through the
gateway, each call's outcome noted for `status.Status`. With no API key AI is off: those calls raise AIOff before anything
is sent. The cost governor may hold a call back instead (HeldBack: OverLimit for one someone asked for at the daily limit,
Deferred for background work).
"""
import json
import os
import re
from collections.abc import Callable
from typing import Any

from mailmind.assistant.application.gateway import DayCost, Gateway
from mailmind.assistant.application.status import Status
from mailmind.assistant.domain import models
from mailmind.assistant.domain.schema import object_in


def parse(out: str) -> tuple[str, int, str, str]:
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        raise ValueError("no JSON")
    obj = json.loads(m.group(0))
    imp = int(obj.get("importance", 2))
    return (str(obj.get("summary", ""))[:600], max(1, min(5, imp)),
            str(obj.get("category", "other"))[:32], str(obj.get("action", ""))[:200])


class Calls:
    MODEL = os.environ.get("MAILMIND_MODEL")  # optional, e.g. claude-haiku-5-5

    def __init__(self, gateway: Callable[[], Gateway], status: Status):
        self._gateway = gateway
        self._status = status

    def noting[T](self, make: Callable[[], T]) -> T:
        """Make a call the way these do, its outcome noted for the status."""
        return self._status.noting(make)

    def engine(self) -> str:
        """"api" when an API key resolves, else "none": AI is off."""
        return "api" if self._gateway().on else "none"

    def model_label(self) -> str:
        """The model name stored beside each AI result and shown under it in the UI."""
        return self.MODEL or models.DEFAULT

    def usage(self, days: int = 30) -> list[DayCost]:
        """Each of the last `days` local days, newest first: its AI calls and their cost in
        micro-dollars, in all and by feature (the usage ledger's report)."""
        return self._gateway().usage(days)

    def call_claude(self, prompt: str, feature: str = "other") -> str:
        """Plain text result (a thread's digest). `feature` names the call in the usage ledger."""
        return self.noting(lambda: self._gateway().ask(prompt, feature=feature, model=self.MODEL).text)

    def call_claude_json(self, prompt: str, schema: dict[str, Any], effort: str | None = None,
                         feature: str = "other") -> dict[str, Any]:
        """Ask Claude for a JSON object matching `schema`. Returns the dict structured output
        (output_config.format) gives, else best-effort regex-parsed JSON from the text. Raises
        RuntimeError ONLY when no reply came (AIOff, one, when AI is off), so the caller can tell
        retry from fallback; a successful-but-unparseable reply yields {}. `feature` names the call in
        the usage ledger."""
        out = self.noting(lambda: self._gateway().ask(prompt, None if os.environ.get("MAILMIND_NO_SCHEMA") else schema,
                                                      feature=feature, model=self.MODEL, effort=effort).text)
        return object_in(out)
