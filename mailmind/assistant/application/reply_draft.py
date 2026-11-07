"""AI 'Draft a reply' via Claude (summarize's API-or-CLI engine). Reuses summarize_thread's
context gather/trim + the global concurrency cap, and summarize's schema-constrained call so the
model returns a guaranteed `{"body": ...}` object. Returns an EDITABLE draft — never sends.
The thread text is fed as UNTRUSTED DATA; the system prompt forbids following instructions in it.
"""
from mailmind.assistant.application.calls import Calls
from mailmind.assistant.application.context import ContextAssembler
from mailmind.assistant.application.ports import Mail, NotSent, Result
from mailmind.assistant.domain import prompts
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger

log = get_logger("ai")
REPLY_SCHEMA = {
    "type": "object",
    "properties": {"body": {"type": "string"}},
    "required": ["body"],
}


class ReplyDrafts:
    def __init__(self, calls: Calls, context: ContextAssembler, uow: UnitOfWork, mail: Mail):
        self._calls = calls
        self._context = context
        self._uow = uow
        self._mail = mail

    def draft(self, account: str, thread_id: str | None = None, pk: int | None = None, instruction: str = "",
              tone: str = "balanced", me: str = "", feature: str = "draft") -> Result:
        """-> (result_dict, http_code). Gathers a thread (thread_id) or a single message (pk).
        `feature` names the call in the usage ledger: "draft-ahead" when it is drafted ahead."""
        with self._uow.read() as c:
            if thread_id:
                rows = self._context.rows(c, account, thread_id)
            elif pk is not None:
                rows = self._mail.message_to_reply_to(c, int(pk))
            else:
                rows = []
        if not rows:
            return {"ok": False, "error": "nothing to reply to"}, 404

        blocks, _omitted = self._context.gather(rows)
        subject = rows[0]["subject"] or "(no subject)"
        tone_word = prompts.TONES.get(tone, prompts.TONES["balanced"])
        instr = (prompts.REPLY_INSTRUCTED.replace("{instruction}", instruction.strip()[:400])
                 if (instruction or "").strip() else prompts.REPLY_UNINSTRUCTED)
        prompt = prompts.REPLY.fill(ctx=("thread" if thread_id else "message"), me=(me or "the user"),
                                    tone=tone_word, instr=instr, subject=subject, blocks="\n".join(blocks))
        try:
            obj = self._calls.call_claude_json(prompt, REPLY_SCHEMA, feature=feature)
        except NotSent:                         # AI off, over the limit, or a draft ahead that must wait
            raise
        except Exception as e:                      # timeout / rate-limit: retryable
            log.warning("draft failed", exc_info=e)
            return {"ok": False, "error": "draft unavailable", "retry": True}, 502
        body = (obj.get("body") or "").strip()
        if not body:
            return {"ok": False, "error": "no draft produced", "retry": True}, 502
        return {"ok": True, "body": body[:6000], "tone": tone, "model": self._calls.model_label()}, 200
