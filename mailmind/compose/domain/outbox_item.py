"""An outbox row: what one is written as, how it's listed, what a try at it leaves, and what it owes once delivered. Values
in and values out. Which row may move from which state is the queue's, each a conditional UPDATE the worker can't race
(`DurableQueue`), so that isn't said again here."""
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
from typing import Any, NotRequired, TypedDict

from mailmind.compose.domain.builder import Built
from mailmind.kernel.row import Row

UNSURE = "May have gone out: the server stopped answering once it had the whole message"
SENT_COPY_PENDING = "Delivered; its copy in Sent is still to be saved"
SENT_COPY_FAILED = "Delivered, but its copy couldn't be saved in Sent"

MAX_ATTEMPTS = 5
BACKOFF_BASE = 20      # secs; doubles per attempt
BACKOFF_CAP = 900
SEND_DELAY = 10        # grace window before the wire send, so Undo Send can cancel it

GRACE, SCHEDULED, BACKOFF = "grace", "scheduled", "backoff"    # what a queued send waits for: outbox.wait_kind
RETRIED: Mapping[str, int | str] = {"wire_begun": 0, "wait_kind": GRACE}   # a person's Retry


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
        "orig_account": orig["account"] if orig else meta["account"] if meta.get("orig_message_id") else None,
        "orig_message_id": orig["message_id"] if orig else meta.get("orig_message_id"),
        "orig_thread_id": orig["thread_id"] if orig else None,
        "original_key": orig["message_key"] if orig else None,
        "envelope": json.dumps(built["envelope"]),
        "recipients": json.dumps({"to": built["to_disp"], "cc": built["cc_disp"],
                                  "bcc": built["bcc_disp"]}),
        "subject": meta.get("subject") or "",
        "raw": built["raw"],
        "draft_id": meta.get("draft_id"),
        "next_attempt_at": max(due, int(meta.get("send_after") or 0)),
        "wait_kind": SCHEDULED if int(meta.get("send_after") or 0) > due else GRACE,
        "created_at": created_at,
    }


def thread_of_reply(r: Row, orig: Row | None) -> str | None:
    """The thread a reply's follow-up lands on: the original's as it stands, else the one it had when this was queued."""
    return (orig["thread_id"] if orig else None) or r["orig_thread_id"]


def is_unsure(r: Row) -> bool:
    """The whole message went out and no answer came, so it may have been delivered. No retry sends it again: a person
    decides, and they are told it might go twice."""
    return bool(r["state"] == "error" and r["wire_begun"] and not r["delivered"])


def refused_for_good(codes: Iterable[object]) -> bool:
    """Whether a server's answer, one code or one for each recipient, refuses for good: every one of them a 5xx. Sent again
    it gets the same answer, and a refused login sent again counts toward a lockout. A 4xx may pass."""
    said = list(codes)
    return bool(said) and all(isinstance(k, int) and 500 <= k < 600 for k in said)


@dataclass(frozen=True)
class FailedTry:
    """What a try at the wire that didn't deliver leaves on its row."""
    give_up: bool                       # to `error` at once, or back in the queue after a back-off
    error: str                          # scrubbed: it is stored and shown
    marks: Mapping[str, int | str]      # the row's own columns it sets
    unsure: bool = False


def failed_try(*, unanswered: bool, permanent: bool, error: str) -> FailedTry:
    """`unanswered`: the whole message went out and no answer came, so it stays marked and only a person sends it again.
    Any other failure either had an answer or never had the whole message out, so the mark goes: `permanent` gives up, since
    the same answer would come, and the rest back off."""
    if unanswered:
        return FailedTry(True, UNSURE, {}, unsure=True)
    return FailedTry(permanent, error, {"wire_begun": 0, "wait_kind": BACKOFF})


@dataclass(frozen=True)
class Owed:
    """What a delivered send still owes, each until its own done-once flag is set. A pass does only these, so a step that
    failed is asked again and one that landed never is."""
    sent_copy: bool                     # a copy to save in Sent
    sent_filed_for_us: bool             # the server files sent mail itself: only the flag is left to set
    answered: bool                      # \\Answered on the original
    followup: bool                      # a follow-up on the thread it answers
    draft: bool                         # the draft it was composed from, superseded now it is truly sent


def owed(r: Row, saves_sent: bool) -> Owed:
    """A brand-new message owes nothing of what a reply does: it has no original, nor a local thread until its Sent copy
    syncs."""
    replied_to = r["orig_message_id"] or r["original_key"]
    return Owed(sent_copy=not saves_sent and not r["sent_appended"],
                sent_filed_for_us=saves_sent and not r["sent_appended"],
                answered=bool(replied_to and not r["answered_set"]),
                followup=bool(replied_to and not r["followup_recorded"]),
                draft=bool(r["draft_id"] and not r["draft_deleted"]))


def sent_copy_to_look_for(r: Row) -> str | None:
    """The Message-ID to look for in Sent before saving a copy: a row delivered before this pass (a retry, or a crash's
    recovery) may have had its last APPEND land. None for one delivered just now, which has saved nothing yet."""
    return r["message_id"] if r["delivered"] else None


def attempts_since_delivery(r: Row) -> int:
    """How many tries a delivered row's bookkeeping has had. They count from the delivery, not on from the send's."""
    return r["attempts"] if r["delivered"] else 0


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
        "unsure": is_unsure(r),
        "created_at": r["created_at"], "sent_at": r["sent_at"],
        "next_attempt_at": na, "scheduled": waiting == SCHEDULED, "retrying": waiting == BACKOFF,
    }
