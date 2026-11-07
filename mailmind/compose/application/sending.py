"""A send: a compose, reply or forward request, built and queued."""
import time
import uuid
from collections.abc import Mapping
from typing import Any

from mailmind.compose.application.composing import Composing
from mailmind.compose.application.outbox import Outbox
from mailmind.compose.application.ports import Book
from mailmind.compose.domain import draft, schedule
from mailmind.compose.domain.builder import build_message
from mailmind.compose.domain.outbox_item import SendMeta
from mailmind.kernel.errors import Refused
from mailmind.platform.db.uow import UnitOfWork


class Sending:
    def __init__(self, uow: UnitOfWork, composing: Composing, outbox: Outbox, book: Book):
        self._uow, self._composing, self._outbox, self._book = uow, composing, outbox, book

    def send(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Validate a compose/reply/forward request, build the message, enqueue it."""
        try:
            send_after = schedule.send_after(body.get("send_after"), time.time())
            spec, in_reply_to_pk, acct = self._composing.spec(body)
            built = build_message(spec)       # validates addresses/headers; raises ValueError
        except ValueError as e:               # our own words: they hold nothing secret
            raise Refused(str(e)) from e
        draft_id = body.get("draft_id")
        key = draft.composer_key(body)
        if not draft_id and key:              # a first save whose answer the composer never heard
            with self._uow.read() as c:
                found = self._book.draft_by_key(c, key)
            draft_id = found["id"] if found else None
        meta: SendMeta = {"account": acct["email"], "idempotency_key": (body.get("idempotency_key") or uuid.uuid4().hex),
                "in_reply_to_pk": in_reply_to_pk, "orig_message_id": spec["in_reply_to"],
                "subject": spec["subject"], "draft_id": draft_id, "to": built["to_disp"], "cc": built["cc_disp"], "bcc": built["bcc_disp"], "send_after": send_after}
        oid, created = self._outbox.enqueue(built, meta)
        return {"ok": True, "outbox_id": oid, "queued": created, "scheduled": bool(send_after),
                "recipients": len(built["envelope"])}
