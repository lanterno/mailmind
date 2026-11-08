"""What a message proposes, read by Claude on a person's click: the meetings and dated tasks in its text, as suggestions they
confirm or edit. One schema-constrained call, and nothing is created by it. The message is UNTRUSTED data, which the system
prompt says, and what comes back is read with care (`domain.extraction`) whatever it holds."""
from datetime import datetime

from mailmind.assistant.application.calls import Calls
from mailmind.assistant.application.context import ContextAssembler
from mailmind.assistant.application.ports import Mail, NotSent, Result
from mailmind.assistant.domain import extraction, prompts
from mailmind.kernel.errors import NotFound, Refused, Unreachable
from mailmind.kernel.ids import Pk
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger

log = get_logger("ai")

_TEXT = {"type": "string"}
EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {"type": "array", "items": {"type": "object",
                                              "properties": {"title": _TEXT, "start": _TEXT, "end": _TEXT, "place": _TEXT},
                                              "required": ["title", "start", "end", "place"]}},
        "tasks": {"type": "array", "items": {"type": "object", "properties": {"title": _TEXT, "due": _TEXT},
                                             "required": ["title", "due"]}},
    },
    "required": ["events", "tasks"],
}


class Extractor:
    def __init__(self, calls: Calls, context: ContextAssembler, uow: UnitOfWork, mail: Mail):
        self._calls = calls
        self._context = context
        self._uow = uow
        self._mail = mail

    def read(self, pk: Pk | None) -> Result:
        if pk is None:
            raise Refused("missing message")
        with self._uow.read() as c:
            rows = self._mail.message_to_reply_to(c, pk)
        if not rows:
            raise NotFound("not found")
        row = rows[0]
        today = datetime.now().astimezone().date()
        prompt = prompts.EXTRACT.fill(today=f"{today:%A, %Y-%m-%d}", sent=row["date_iso"] or "an unknown day",
                                      frm=row["from_name"] or row["from_addr"] or "someone",
                                      subject=row["subject"] or "(no subject)",
                                      body=extraction.readable(self._context.body_of(row)))
        try:
            answer = self._calls.call_claude_json(prompt, EXTRACT_SCHEMA, feature="extract")
        except NotSent:                         # AI off, or over the daily limit: the route answers it
            raise
        except Exception as e:
            log.warning("extract failed", exc_info=e)
            raise Unreachable("couldn’t read this message for dates", retry=True)
        return {"ok": True, **extraction.suggestions(answer, today), "model": self._calls.model_label()}
