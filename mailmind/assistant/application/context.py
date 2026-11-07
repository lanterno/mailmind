"""What the model reads of a conversation: the first message and the last 19, each trimmed of its quotes and within the
caps. A message whose body hasn't come down yet is asked of the Mailbox, at interactive priority: the fetch is the Mail
port's, and nothing here speaks to a server. Thread summaries and replies both read through this."""
import re

from mailmind.assistant.application.ports import ConversationBy, Mail
from mailmind.kernel.row import Connection, Row
from mailmind.platform.workers import gates

CAP_MSGS = 20
CAP_PER_MSG = 1500
CAP_TOTAL = 14000
_QUOTE = re.compile(r"^\s*>")
_ATTR = re.compile(r"^(On .+ wrote:|From:.*|-{3,} ?Forwarded)", re.I)


def lone_key(row: Row) -> str:
    """What a message with no thread goes by here: its Message-ID, or if it has none, its pk."""
    return f"m:{row['message_id']}" if row["message_id"] else f"pk:{row['pk']}"


def _trim(text: str | None) -> str:
    out: list[str] = []
    for ln in (text or "").splitlines():
        if _QUOTE.match(ln):
            continue
        if _ATTR.match(ln.strip()):
            break
        out.append(ln)
    return "\n".join(out).strip()[:CAP_PER_MSG]


class ContextAssembler:
    def __init__(self, mail: Mail):
        self._mail = mail

    def rows(self, c: Connection, account: str, thread_id: str) -> list[Row]:
        kind, _, value = str(thread_id).partition(":")
        lone: dict[str, tuple[ConversationBy, str]] = {"m": ("message_id", value), "pk": ("pk", value)}
        by, value = lone.get(kind, ("thread_id", thread_id))
        rows = self._mail.conversation_rows(c, account, by, value)
        seen: set[str] = set()
        out: list[Row] = []
        for r in rows:
            k = (r["message_id"] or "").strip() or f"pk:{r['pk']}"
            if k in seen:
                continue
            seen.add(k)
            out.append(r)
        return out

    def gather(self, rows: list[Row]) -> tuple[list[str], int]:
        omitted = 0
        if len(rows) > CAP_MSGS:                       # keep the first (context) + the most recent
            omitted = len(rows) - CAP_MSGS
            rows = [rows[0]] + rows[-(CAP_MSGS - 1):]
        blocks: list[str] = []
        total = 0
        for i, r in enumerate(rows, 1):
            body = r["body_text"] or r["snippet"] or ""
            if not body and not r["body_fetched"]:     # only hit IMAP for body-less members
                try:
                    parsed = gates.run(r["account"], lambda: self._mail.fetch_body(r["pk"]),
                                       priority=gates.Priority.INTERACTIVE)
                    body = (parsed["text"] if parsed else None) or ""
                except Exception:
                    body = ""
            body = _trim(body)
            if total + len(body) > CAP_TOTAL:
                body = body[:max(0, CAP_TOTAL - total)]
            total += len(body)
            who = r["from_name"] or r["from_addr"] or "someone"
            blocks.append(f'--- [{i}] {who} <{r["from_addr"] or ""}> — {r["date_iso"] or ""}\n{body}')
            if total >= CAP_TOTAL:
                break
        return blocks, omitted
