"""Ask-your-inbox — natural-language Q&A over the user's own mail, fully local.

Retrieve-then-read RAG, no embeddings: FTS5 surfaces the most relevant messages, a
bounded slice of their text (real body → cached triage summary → snippet) is handed to
Claude (summarize's API-or-CLI engine) under a strict schema, and the model returns a prose
answer plus the pks it actually used. Citations are intersected with what was retrieved+shown,
so the model can never cite (or leak) a message outside the account-scoped candidate set.

  result = assistant.asker.ask(account, question)   # account="all" → across every enabled mailbox

Read-only: only SELECTs, no IMAP fetch (never blocks), no mutation, no SMTP. Shares the
2-process AI cap with the other bridges. The mail never leaves 127.0.0.1.
"""
from datetime import datetime, timezone
import re

from mailmind.assistant.application.calls import Calls
from mailmind.assistant.application.ports import Mail, NotSent, Result
from mailmind.assistant.domain import citations, prompts
from mailmind.kernel.errors import Refused, Unreachable
from mailmind.kernel.row import Row

MAX_QUESTION = 8000     # defensive re-trim (the route already caps)
CAND_LIMIT = 40         # FTS rows pulled before body-coverage selection
READ_MAX = 14           # candidates whose text actually lands in the prompt (~12-15 bounded bodies)
CAP_PER_MSG = 1200      # per-candidate char cap
CAP_TOTAL = 14000       # cumulative untrusted-text budget
# common words carry no retrieval signal; drop them so FTS focuses on content terms (it's
# implicit-OR over barewords, so noise words otherwise pull in broad, irrelevant candidates)
_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "did", "do", "does", "for",
    "from", "had", "has", "have", "how", "i", "in", "is", "it", "its", "me", "my", "of",
    "on", "or", "that", "the", "their", "them", "they", "this", "to", "was", "we", "were",
    "what", "when", "where", "which", "who", "whom", "why", "will", "with", "you", "your",
    "about", "any", "can", "could", "would", "should", "there", "these", "those", "am",
}
ASK_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "cited_pks": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["answer"],
}


class Asker:
    def __init__(self, calls: Calls, mail: Mail):
        self._calls = calls
        self._mail = mail

    def _terms(self, question: str) -> list[str]:
        """Content terms of an NL question: trailing punctuation + stopwords dropped, FTS-quoted.
        Returns a list of quoted phrase tokens (each safe to OR into a MATCH)."""
        out: list[str] = []
        for w in re.findall(r"[^\s]+", question):
            t = re.sub(r"^[^\w@.]+|[^\w@.]+$", "", w)        # trim leading/trailing punctuation
            if len(t) >= 2 and t.lower() not in _STOP and len(out) < 12:
                out.append('"' + t.replace('"', '""') + '"')
        return out

    def _fts_match(self, question: str) -> str:
        """OR-join the content terms into one FTS5 MATCH string (recall-friendly for Q&A — FTS5's
        implicit operator is AND, which is far too strict for a question). '' → no usable terms."""
        return " OR ".join(self._terms(question))

    def _scope(self, account: str | None) -> str | list[str]:
        """What confines retrieval to the validated mailbox(es): the one asked about. 'all'/empty →
        every enabled account (never widened by anything in the question), and none matches nothing."""
        if account and account != "all":
            return account
        return [a["email"] for a in self._mail.accounts()]

    def ask(self, account: str | None, question: str | None) -> Result:
        question = (question or "").strip()[:MAX_QUESTION]
        if not question:
            raise Refused("question required")

        found = self._mail.for_asking(self._fts_match(question), self._scope(account), CAND_LIMIT)
        if found is None:
            raise Refused("bad query")
        once: set[int] = set()                                # a message counts once, however many folders hold it
        rows = [r for r in found
                if not ((r["message_key"] or -r["pk"]) in once or once.add(r["message_key"] or -r["pk"]))]

        if not rows:
            return {"ok": True, "account": account, "question": question,
                    "answer": "I couldn’t find any messages matching that in your mail.",
                    "sources": [], "model": self._calls.model_label(),
                    "generated": datetime.now(timezone.utc).isoformat()}

        # body-coverage cascade + bounded packing; only packed pks are citable
        blocks: list[str] = []
        by_pk: dict[int, Row] = {}
        total = 0
        for r in rows:
            if len(blocks) >= READ_MAX or total >= CAP_TOTAL:
                break
            text = (r["body_text"] or r["ai_summary"] or r["snippet"] or "").strip()
            if not text:
                continue
            text = text[:CAP_PER_MSG]
            if total + len(text) > CAP_TOTAL:
                text = text[:CAP_TOTAL - total]
            total += len(text)
            pk = r["pk"]
            by_pk[pk] = r
            head = f"[{pk}] {r['from_name'] or r['from_addr']} — {r['subject'] or '(no subject)'} ({r['date_iso'] or ''})"
            blocks.append(head + "\n" + text)

        if not blocks:
            return {"ok": True, "account": account, "question": question,
                    "answer": "I found related messages but none have readable text indexed yet.",
                    "sources": [], "model": self._calls.model_label(),
                    "generated": datetime.now(timezone.utc).isoformat()}

        prompt = prompts.ASK.fill(question=question, blocks="\n\n".join(blocks))
        try:
            obj = self._calls.call_claude_json(prompt, ASK_SCHEMA, feature="ask")
        except NotSent:                         # AI off, or over the daily limit: the route answers it
            raise
        except Exception:
            raise Unreachable("ask unavailable", retry=True)

        answer = (str(obj.get("answer")) if obj.get("answer") else "").strip()[:4000]
        if not answer:
            raise Unreachable("ask unavailable", retry=True)

        cited = citations.in_answer(obj.get("cited_pks"), set(by_pk))     # what was account-scoped AND shown to the model
        sources = [{
            "key": by_pk[pk]["message_key"], "folder": by_pk[pk]["folder"],
            "from": by_pk[pk]["from_name"] or by_pk[pk]["from_addr"],
            "subject": by_pk[pk]["subject"] or "(no subject)",
            "snippet": (by_pk[pk]["snippet"] or "")[:160],
            "account": by_pk[pk]["account"], "date": by_pk[pk]["date_iso"] or "",
        } for pk in cited]

        return {"ok": True, "account": account, "question": question, "answer": answer,
                "sources": sources, "model": self._calls.model_label(),
                "generated": datetime.now(timezone.utc).isoformat()}
