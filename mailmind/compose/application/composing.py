"""What a save and a send share: a request made into the message it asks for."""
from collections.abc import Mapping
from typing import Any

from mailmind.compose.application.ports import Accounts, Account, Book, Messages, Screen
from mailmind.compose.domain import composition
from mailmind.compose.domain.attachments import Attachment, decode as attachments_of, extract, take_along
from mailmind.compose.domain.builder import Composed
from mailmind.compose.domain.resume import REPLIES
from mailmind.content.mime import Parsed
from mailmind.kernel.errors import NotFound, Refused, Unreachable
from mailmind.kernel.parsed import as_int, is_object
from mailmind.kernel.row import Row
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger
from mailmind.platform.workers import gates

log = get_logger("compose")


class Composing:
    def __init__(self, uow: UnitOfWork, book: Book, accounts: Accounts, mail: Messages, screen: Screen):
        self._uow, self._book, self._accounts, self._mail, self._screen = uow, book, accounts, mail, screen

    def spec(self, body: Mapping[str, Any]) -> tuple[Composed, int | None, Account]:
        """Shared by send + save-draft: resolve account, decode attachments, assemble
        reply/forward bodies + threading. Returns (spec, in_reply_to_pk, acct)."""
        mode = body.get("mode", "new")
        try:
            acct = self._accounts.get((body.get("account") or "").strip())   # From: only a configured account
        except KeyError:
            raise Refused("unknown sending account")
        text = body.get("text") or ""
        in_reply_to_pk: int | None = None
        forwarded: list[Attachment] = []    # the original's files a forward added to the payload's
        attachments = attachments_of(body.get("attachments"))
        resume = body.get("resume")
        if resume is not None:
            if not is_object(resume):
                raise Refused("bad draft data")
            tail = composition.tail_of(resume)
            made = composition.as_drafted(text, tail, self._recorded_quote(body, tail), resume.get("in_reply_to"),
                                          resume.get("references"))
            if mode in REPLIES and made.in_reply_to:
                orow = self._reopened_original(body, acct, made.in_reply_to)
                in_reply_to_pk = orow["pk"] if orow else None
        elif mode in (*REPLIES, "forward"):
            opk = as_int(body.get("in_reply_to_pk"))
            if opk is None:
                raise Refused("missing original message")
            orow = self._mail.message(opk)
            if not orow:
                raise NotFound("original message not found")
            try:
                parsed: Parsed | dict[str, Any] = gates.run(orow["account"], lambda: self._mail.fetch_body(opk),
                                                            priority=gates.Priority.INTERACTIVE) or {}
            except Exception as e:
                log.warning("original fetch failed for pk %(id)s: %(why)s", {"id": opk, "why": self._screen.quiet_error(e)})
                raise Unreachable("could not load the original message")
            if mode == "forward":
                made = composition.as_forward(text, orow, parsed)
                if body.get("forward_attachments", True):
                    forwarded = take_along(attachments, extract(self._mail.body(opk)))
                    attachments.extend(forwarded)
            else:
                made = composition.as_reply(text, orow, parsed)
                in_reply_to_pk = opk
        else:
            made = composition.as_typed(text)
        return composition.composed(acct, body, made, attachments, forwarded), in_reply_to_pk, acct

    def _recorded_quote(self, body: Mapping[str, Any], tail: str) -> Row | None:
        did = as_int(body.get("draft_id"))
        if not tail or not did:
            return None
        with self._uow.read() as c:
            return self._book.draft_quote(c, did)

    def _reopened_original(self, body: Mapping[str, Any], acct: Account, in_reply_to: str) -> Row | None:
        """By Message-ID: a pk may have been renumbered. Past that From changed, so it is another account's, and only the
        request's pk can name it."""
        named = body.get("in_reply_to_pk")
        return (self._mail.by_msgid(acct["email"], in_reply_to)
                or composition.named_original(self._mail.message(named) if isinstance(named, int) else None, in_reply_to))
