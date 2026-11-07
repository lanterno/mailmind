"""AI rewrite/tone/translate for the composer, via Claude (summarize's API-or-CLI engine).
One schema-constrained call returning {"text": ...}; shares summarize_thread's 2-proc cap. The
input is UNTRUSTED data — the system prompt forbids following instructions inside it; output is plain
text only.
"""
from mailmind.assistant.application.calls import Calls
from mailmind.assistant.application.ports import NotSent, Result
from mailmind.assistant.domain import prompts
from mailmind.kernel.errors import Refused, Unreachable
from mailmind.platform.logging import get_logger

log = get_logger("ai")
REWRITE_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}


class Rewriter:
    def __init__(self, calls: Calls):
        self._calls = calls

    def rewrite(self, text: str | None, op: str, lang: str = "English") -> Result:
        text = (text or "").strip()
        if not text:
            raise Refused("nothing to rewrite")
        instr = prompts.REWRITE_OPS.get(op)
        if not instr:
            raise Refused("unknown op")
        prompt = prompts.REWRITE.fill(instr=instr.replace("{lang}", (lang or "English")[:40]), text=text[:8000])
        try:
            obj = self._calls.call_claude_json(prompt, REWRITE_SCHEMA, feature="rewrite")
        except NotSent:                         # AI off, or over the daily limit: the route answers it
            raise
        except Exception as e:
            log.warning("rewrite failed", exc_info=e)
            raise Unreachable("rewrite unavailable", retry=True)
        out = (obj.get("text") or "").strip()
        if not out:
            raise Unreachable("no rewrite produced", retry=True)
        return {"ok": True, "text": out[:12000], "op": op}
