"""What one sender has been writing about: a call over their recent threads, on a person's click, from what is stored of them. No
body is fetched and nothing is kept."""
from typing import Any

from mailmind.assistant.application.calls import Calls
from mailmind.assistant.application.ports import Mail, NotSent, Result
from mailmind.assistant.domain import prompts, sender_digest
from mailmind.kernel.errors import NotFound, Refused, Unreachable
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger

log = get_logger("ai")
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "topics": {"type": "array", "items": {"type": "string"}},
        "open": {"type": "string"},
    },
    "required": ["summary", "topics", "open"],
}


class SenderDigest:
    def __init__(self, calls: Calls, uow: UnitOfWork, mail: Mail):
        self._calls = calls
        self._uow = uow
        self._mail = mail

    def summarize(self, raw_addr: object) -> Result:
        """A sender's recent threads, in a few sentences. Raises Refused for an address that won't read and NotFound for
        a sender with no mail stored."""
        addr = sender_digest.address(raw_addr)
        if addr is None:
            raise Refused("bad address")
        with self._uow.read() as c:
            rows = self._mail.sender_recent(c, addr, sender_digest.RECENT)
        if not rows:
            raise NotFound("no mail from this sender")
        threads = sender_digest.threads_of(rows)
        who = f'{rows[0]["from_name"] or addr} <{addr}>'
        prompt = prompts.SENDER.fill(who=who, n=len(threads), threads=sender_digest.prompt_lines(threads))
        try:
            obj = self._calls.call_claude_json(prompt, _SCHEMA, feature="sender")
        except NotSent:                     # AI off, or over the daily limit: the route answers it
            raise
        except Exception as e:
            log.warning("sender summary failed", exc_info=e)
            raise Unreachable("summary unavailable", retry=True)
        said = sender_digest.clean(obj)
        if not said["summary"]:             # replied, but with nothing in it
            said = sender_digest.fallback(threads)
        return {"ok": True, "addr": addr, "threads": len(threads), **said}
