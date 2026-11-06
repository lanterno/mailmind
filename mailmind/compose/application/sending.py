"""A send: a compose, reply or forward request, built and queued."""
import time
import uuid

from mailmind.compose.application.composing import composer_key
from mailmind.compose.domain.builder import build_message
from mailmind.compose.domain.errors import Refused


class Sending:
    def __init__(self, composing, outbox, book):
        self._composing, self._outbox, self._book = composing, outbox, book

    def send(self, body):
        """Validate a compose/reply/forward request, build the message, enqueue it."""
        send_after = 0
        try:                                  # send-later: epoch secs; 0/absent = now (after the grace window)
            sa = int(body.get("send_after") or 0)
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad send time"}, 400
        if sa:                                # refused before the spec, so no original-body fetch
            now = time.time()
            if sa <= now:                     # never quietly turn a missed schedule into "send now"
                return {"ok": False, "error": "that send time has passed"}, 400
            if sa >= now + 366 * 86400:
                return {"ok": False, "error": "pick a send time within a year"}, 400
            send_after = sa
        try:
            spec, in_reply_to_pk, acct = self._composing.spec(body)
            built = build_message(spec)       # validates addresses/headers; raises ValueError
        except Refused as h:
            return {"ok": False, "error": h.msg}, h.code
        except ValueError as e:
            return {"ok": False, "error": str(e)}, 400
        draft_id = body.get("draft_id")
        if not draft_id and composer_key(body):    # a first save whose answer the composer never heard
            c = self._book.connect()
            try:
                found = self._book.draft_by_key(c, composer_key(body))
            finally:
                c.close()
            draft_id = found["id"] if found else None
        meta = {"account": acct["email"], "idempotency_key": (body.get("idempotency_key") or uuid.uuid4().hex),
                "in_reply_to_pk": in_reply_to_pk, "orig_message_id": spec["in_reply_to"],
                "subject": spec["subject"], "draft_id": draft_id, "to": built["to_disp"], "cc": built["cc_disp"], "bcc": built["bcc_disp"], "send_after": send_after}
        oid, created = self._outbox.enqueue(built, meta)
        return {"ok": True, "outbox_id": oid, "queued": created, "scheduled": bool(send_after),
                "recipients": len(built["envelope"])}, 200
