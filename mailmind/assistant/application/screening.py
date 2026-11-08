"""The AI's suggestions for the Screener: a pass over the senders waiting there, on a person's click, which says Allow or Screen
out only of those it is sure of. It suggests and nothing else. It has no way to the rules: a person's click on a row does that,
through Curation, as it always did."""
from typing import Any

from mailmind.assistant.application.calls import Calls
from mailmind.assistant.application.ports import Mail, NotSent, Result
from mailmind.assistant.domain import prompts, screening
from mailmind.kernel.errors import Unreachable
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger

log = get_logger("ai")
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "verdict": {"type": "string", "enum": ["keep", "block"]},
                "confidence": {"type": "number"},
                "why": {"type": "string"},
            },
            "required": ["id", "verdict", "confidence", "why"],
        }},
    },
    "required": ["verdicts"],
}


class Screening:
    def __init__(self, calls: Calls, uow: UnitOfWork, mail: Mail):
        self._calls = calls
        self._uow = uow
        self._mail = mail

    def suggest(self, account: str | None) -> Result:
        """Who of those waiting in this account's Screener the AI would allow or screen out, and why, where it is sure."""
        waiting = self._mail.screener_items(account)[:screening.MAX_SENDERS]
        if not waiting:
            return {"ok": True, "account": account, "asked": 0, "suggestions": []}
        with self._uow.read() as c:
            lines = [screening.line(n, s, self._mail.sender_recent(c, s["from_addr"], screening.SUBJECTS))
                     for n, s in enumerate(waiting, 1)]
        try:
            obj = self._calls.call_claude_json(prompts.SCREEN.fill(senders="\n".join(lines)), _SCHEMA, feature="screen")
        except NotSent:                     # AI off, or over the daily limit: the route answers it
            raise
        except Exception as e:
            log.warning("screener suggestions failed", exc_info=e)
            raise Unreachable("suggestions unavailable", retry=True)
        return {"ok": True, "account": account, "asked": len(waiting),
                "suggestions": screening.clean(obj, [s["from_addr"] for s in waiting])}
