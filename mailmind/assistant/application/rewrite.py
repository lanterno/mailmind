"""AI rewrite/tone/translate for the composer, via Claude (summarize's API-or-CLI engine).
One schema-constrained call returning {"text": ...}; shares summarize_thread's 2-proc cap. The
input is UNTRUSTED data — the system prompt forbids following instructions inside it; output is plain
text only.
"""
from mailmind.assistant.application.calls import Calls
from mailmind.assistant.application.ports import NotSent, Result
from mailmind.assistant.domain import prompts
from mailmind.platform.logging import get_logger

log = get_logger("ai")
REWRITE_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}


class Rewriter:
    def __init__(self, calls: Calls):
        self._calls = calls

    def rewrite(self, text: str | None, op: str, lang: str = "English") -> Result:
        """-> (result_dict, http_code)."""
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "nothing to rewrite"}, 400
        instr = prompts.REWRITE_OPS.get(op)
        if not instr:
            return {"ok": False, "error": "unknown op"}, 400
        prompt = prompts.REWRITE.fill(instr=instr.replace("{lang}", (lang or "English")[:40]), text=text[:8000])
        try:
            obj = self._calls.call_claude_json(prompt, REWRITE_SCHEMA, feature="rewrite")
        except NotSent:                         # AI off, or over the daily limit: the route answers it
            raise
        except Exception as e:
            log.warning("rewrite failed", exc_info=e)
            return {"ok": False, "error": "rewrite unavailable", "retry": True}, 502
        out = (obj.get("text") or "").strip()
        if not out:
            return {"ok": False, "error": "no rewrite produced", "retry": True}, 502
        return {"ok": True, "text": out[:12000], "op": op}, 200
