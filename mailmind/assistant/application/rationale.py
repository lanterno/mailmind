"""Why each sender a person marked is worth cleaning up: one call over their stored numbers, for the preview a batch cleanup shows.
It says why and does nothing: what a person confirms goes through Curation."""
import time
from typing import Any

from mailmind.assistant.application.calls import Calls
from mailmind.assistant.application.ports import Mail, NotSent, Result
from mailmind.assistant.domain import prompts, rationale
from mailmind.kernel.errors import Refused, Unreachable
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger

log = get_logger("ai")
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "rationales": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
            "required": ["id", "text"],
        }},
    },
    "required": ["rationales"],
}


class Rationales:
    def __init__(self, calls: Calls, uow: UnitOfWork, mail: Mail):
        self._calls = calls
        self._uow = uow
        self._mail = mail

    def explain(self, senders: object) -> Result:
        """A sentence for each of `senders` that has mail stored. Raises Refused for a list that won't read."""
        addrs = rationale.named(senders)
        if addrs is None:
            raise Refused("bad senders")
        with self._uow.read() as c:
            rows = self._mail.sender_stats(c, addrs)
        if not rows:
            return {"ok": True, "rationales": {}}
        now = time.time()
        prompt = prompts.RATIONALE.fill(senders="\n".join(rationale.line(n, r, now) for n, r in enumerate(rows, 1)))
        try:
            obj = self._calls.call_claude_json(prompt, _SCHEMA, feature="rationale")
        except NotSent:                     # AI off, or over the daily limit: the route answers it
            raise
        except Exception as e:
            log.warning("cleanup rationale failed", exc_info=e)
            raise Unreachable("rationale unavailable", retry=True)
        return {"ok": True, "rationales": rationale.clean(obj, rows, now)}
