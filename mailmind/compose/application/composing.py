"""What a save and a send share: a request made into the message it asks for."""
from collections.abc import Mapping
from typing import Any

from mailmind.compose.application.ports import Accounts, Account, Book, Messages, Screen
from mailmind.compose.domain import limits
from mailmind.compose.domain.attachments import Attachment, decode as attachments_of, extract
from mailmind.compose.domain.builder import Composed, reference_chain, thread_ids
from mailmind.compose.domain.errors import Refused
from mailmind.compose.domain.forward import forward_bodies
from mailmind.compose.domain.reply import Original, reply_bodies
from mailmind.compose.domain.resume import lf
from mailmind.content import sanitize
from mailmind.content.mime import Parsed
from mailmind.content.quote import own_html, quote_tail
from mailmind.kernel.parsed import as_int, is_object
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger
from mailmind.platform.workers import gates

log = get_logger("compose")


class Composing:
    def __init__(self, uow: UnitOfWork, book: Book, accounts: Accounts, mail: Messages, screen: Screen):
        self._uow, self._book, self._accounts, self._mail, self._screen = uow, book, accounts, mail, screen

    def spec(self, body: Mapping[str, Any]) -> tuple[Composed, int | None, Account]:
        """Shared by send + save-draft: resolve account, decode attachments, assemble
        reply/forward bodies + threading. Returns (spec, in_reply_to_pk, acct). Raises Refused."""
        mode = body.get("mode", "new")
        try:
            acct = self._accounts.get((body.get("account") or "").strip())   # From: only a configured account
        except KeyError:
            raise Refused(400, "unknown sending account")
        text = body.get("text") or ""
        own = text                      # what you typed, before any quote is added
        tail_text: str | None = None
        tail_html: str | None = None
        html: str | None = None
        in_reply_to: str | None = None
        references: list[str] | None = None
        in_reply_to_pk: int | None = None
        forwarded: list[Attachment] = []    # the original's files a forward added to the payload's
        subject = body.get("subject") or ""
        attachments = attachments_of(body.get("attachments"))
        resume = body.get("resume")
        if resume is not None:
            # A reopened draft (draft_resume) goes out as drafted: your text plus the quote or forwarded
            # section it already carried, its own threading headers, and only the files in the payload.
            # The original is never fetched, re-quoted or re-attached.
            if not is_object(resume):
                raise Refused(400, "bad draft data")
            own = lf(text)
            tail = lf(str(resume.get("tail_text") or ""))
            text = own + tail
            if tail:   # the quote's HTML only when this draft's record vouches for that exact quote
                did = as_int(body.get("draft_id"))
                with self._uow.read() as c:
                    dr = self._book.draft_quote(c, did) if did else None
                if dr and dr["tail_html"] and lf(dr["tail_text"] or "") == tail:
                    # built and sanitized by us at that save; never re-sanitized
                    tail_html = sanitize.without_fold(dr["tail_html"])
                    html = own_html(own) + tail_html
            tail_text = tail or None
            in_reply_to, references = thread_ids(resume.get("in_reply_to"), resume.get("references"))
            if mode in ("reply", "replyall") and in_reply_to:   # by Message-ID: a pk may have been renumbered
                orow = self._mail.by_msgid(acct["email"], in_reply_to)
                if not orow:                                    # From changed: it is another account's
                    named = self._mail.message(body["in_reply_to_pk"]) if isinstance(body.get("in_reply_to_pk"), int) else None
                    orow = named if named and named["message_id"] == in_reply_to else None
                in_reply_to_pk = orow["pk"] if orow else None
        elif mode in ("reply", "replyall", "forward"):
            opk = as_int(body.get("in_reply_to_pk"))
            if opk is None:
                raise Refused(400, "missing original message")
            orow = self._mail.message(opk)
            if not orow:
                raise Refused(404, "original message not found")
            try:
                parsed: Parsed | dict[str, Any] = gates.run(orow["account"], lambda: self._mail.fetch_body(opk),
                                                            priority=gates.Priority.INTERACTIVE) or {}
            except Exception as e:
                log.warning("original fetch failed for pk %(id)s: %(why)s", {"id": opk, "why": self._screen.quiet_error(e)})
                raise Refused(502, "could not load the original message")
            orig: Original = {"from_name": orow["from_name"], "from_addr": orow["from_addr"],
                    "subject": orow["subject"], "date": parsed.get("date"),
                    "to": parsed.get("to"), "cc": parsed.get("cc"),
                    "text": parsed.get("text"), "html": parsed.get("html")}
            if mode == "forward":
                text, html = forward_bodies(text, orig)
                if body.get("forward_attachments", True):
                    raw = self._mail.body(opk)
                    existing = sum(len(a["data"]) for a in attachments)
                    for a in extract(raw):
                        if existing + len(a["data"]) > limits.MAX_ATTACH_BYTES:
                            break
                        existing += len(a["data"])
                        attachments.append(a)
                        forwarded.append(a)
            else:
                text, html = reply_bodies(text, orig)
                in_reply_to = orow["message_id"]
                references = reference_chain(orow["message_id"], orow["refs"], orow["in_reply_to"])
                in_reply_to_pk = opk
            tail_text, tail_html = quote_tail(own, text, html)
        spec: Composed = {
            "account": acct["email"], "account_name": acct.get("alias") or acct["email"],
            "to": body.get("to") or [], "cc": body.get("cc") or [], "bcc": body.get("bcc") or [],
            "subject": subject, "text": text, "html": html, "attachments": attachments,
            "in_reply_to": in_reply_to, "references": references,
            "own": own, "tail": {"text": tail_text, "html": tail_html},   # recorded by do_draft; build_message ignores them
            "forwarded": forwarded,
        }
        return spec, in_reply_to_pk, acct
