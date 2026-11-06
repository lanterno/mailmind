"""An outbox row: what one is written as, and how it's listed."""
import json
from typing import Any, NotRequired, TypedDict

from mailmind.compose.domain.builder import Built
from mailmind.kernel.row import Row

UNSURE = "May have gone out: the server stopped answering once it had the whole message"
SENT_COPY_PENDING = "Delivered; its copy in Sent is still to be saved"
SENT_COPY_FAILED = "Delivered, but its copy couldn't be saved in Sent"


class SendMeta(TypedDict):
    """What goes with a built message into the outbox."""
    account: str
    idempotency_key: str
    in_reply_to_pk: NotRequired[int | None]
    orig_message_id: NotRequired[str | None]
    subject: NotRequired[str | None]
    draft_id: NotRequired[int | None]
    to: NotRequired[list[str]]
    cc: NotRequired[list[str]]
    bcc: NotRequired[list[str]]
    send_after: NotRequired[int]


class Listed(TypedDict):
    """An outbox row as the page sees it."""
    id: int
    account: str
    subject: str
    to: list[str]
    cc: list[str]
    bcc_count: int
    state: str
    attempts: int
    error: str | None
    unsure: bool
    created_at: str
    sent_at: str | None
    next_attempt_at: int
    scheduled: bool
    retrying: bool


def new_row(built: Built, meta: SendMeta, orig: Row | None, due: int, created_at: str) -> dict[str, Any]:
    """`built` is what build_message gave, `orig` the row of the message it replies to, if any is left.
    `due` is the end of the grace window: a later send_after moves it on. The queue goes by this column,
    so a send scheduled for later needs nothing of the worker, and can be cancelled until it goes."""
    return {
        "account": meta["account"],
        "idempotency_key": meta["idempotency_key"],
        "message_id": built["message_id"],
        "in_reply_to_pk": meta.get("in_reply_to_pk"),
        "orig_account": orig["account"] if orig else meta["account"] if meta.get("orig_message_id") else None,
        "orig_message_id": orig["message_id"] if orig else meta.get("orig_message_id"),
        "orig_thread_id": orig["thread_id"] if orig else None,
        "envelope": json.dumps(built["envelope"]),
        "recipients": json.dumps({"to": built["to_disp"], "cc": built["cc_disp"],
                                  "bcc": built["bcc_disp"]}),
        "subject": meta.get("subject") or "",
        "raw": built["raw"],
        "draft_id": meta.get("draft_id"),
        "next_attempt_at": max(due, int(meta.get("send_after") or 0)),
        "wait_kind": "scheduled" if int(meta.get("send_after") or 0) > due else "grace",
        "created_at": created_at,
    }


def listed(r: Row) -> Listed:
    """A row as the outbox lists it."""
    rcpt: dict[str, Any] = {}
    try:
        rcpt = json.loads(r["recipients"]) or {}
    except Exception:
        pass
    na = r["next_attempt_at"] or 0
    state, error = r["state"], r["last_error"]
    if r["delivered"] and state != "sent":           # went out
        if r["sent_appended"]:                       # what's left of it asks nothing of anyone
            state, error = "sent", None
        else:                                        # its Sent copy is owed
            state, error = ("error", SENT_COPY_FAILED) if state == "error" else ("sent", SENT_COPY_PENDING)
    waiting = r["wait_kind"] if state == "queued" else None
    return {
        "id": r["id"], "account": r["account"], "subject": r["subject"] or "(no subject)",
        "to": rcpt.get("to", []), "cc": rcpt.get("cc", []), "bcc_count": len(rcpt.get("bcc", [])),
        "state": state, "attempts": r["attempts"], "error": error,
        "unsure": bool(state == "error" and r["wire_begun"] and not r["delivered"]),
        "created_at": r["created_at"], "sent_at": r["sent_at"],
        "next_attempt_at": na, "scheduled": waiting == "scheduled", "retrying": waiting == "backoff",
    }
