"""What the page asks of Compose, and what it answers."""
from typing import Any, Literal

from mailmind.http.schemas.common import Answer, Failure, Flag, Number, QueryArgs, RequestBody

# As a person typed them: a string of addresses, or one for each chip (`addresses.parse_addr_list`, which judges each).
Recipients = str | list[str] | None


class OutboxArgs(QueryArgs):
    limit: Number = 50


class NamesASend(RequestBody):
    id: Number                                # going without one was always `bad request`


class Composition(RequestBody):
    """What a send and a save share (`Composing.spec`, `composition.composed`, `draft.composer_key`). `mode` left out is a new
    message, and `forward_attachments` left out takes the original's files along, so what was left out must stay out."""
    account: str | None = None
    mode: str | None = None
    to: Recipients = None
    cc: Recipients = None
    bcc: Recipients = None
    subject: str | None = None
    text: str | None = None
    attachments: list[Any] | None = None      # each file is `attachments.decode`'s to judge, in its own words
    resume: dict[str, Any] | None = None      # its fields are `composition.as_drafted`'s, which reads what's there as text
    in_reply_to_pk: Number | None = None
    forward_attachments: Flag | None = None
    draft_id: Number | None = None
    idempotency_key: str | None = None


class SendBody(Composition):
    send_after: Number | None = None          # 0 or none is now, and `schedule.send_after` judges a time that reads as one


class DraftBody(Composition):
    delete: Flag | None = None
    autosave: Flag | None = None
    base_version: Number | None = None        # none overwrites, and 0 is a version


class ResumeBody(RequestBody):
    draft_id: Number | None = None
    pk: Number | None = None


class OutboxItem(Answer):
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


class WaitingDraft(Answer):
    id: int
    account: str
    subject: str
    saved_at: int
    attempts: int
    discarding: bool


class Outbox(Answer):
    ok: Literal[True]
    items: list[OutboxItem]
    drafts: list[WaitingDraft]


class Retried(Answer):
    ok: Literal[True]
    gone: Literal[True] | None = None         # it had gone out: only its bookkeeping is left


class Cancelled(Answer):
    ok: bool                                  # false with a 200: too late to cancel


class SentNow(Answer):
    ok: Literal[True]


class Queued(Answer):
    ok: Literal[True]
    outbox_id: int
    queued: bool
    scheduled: bool
    recipients: int


class DraftFile(Answer):
    filename: str
    content_type: str
    size: int
    b64: str


class HandedOver(Answer):
    in_reply_to: str
    references: str
    tail_text: str


class DraftSaved(Answer):
    ok: Literal[True]
    draft_id: int
    version: int
    message_id: str
    pushed: bool
    uid: int | None = None
    resume: HandedOver | None = None          # a first save's, with the original's files, so it needs that row no longer
    files: list[DraftFile] | None = None


class NothingToSave(Answer):
    ok: Literal[True]
    draft_id: int | None
    empty: Literal[True]


class DraftDiscarded(Answer):
    ok: Literal[True]
    deleted: Literal[True]


class DraftChanged(Failure):
    """A save that never saw the latest (`draft.CHANGED_ELSEWHERE`), or any other 409, which says what `Failure` says."""
    conflict: Literal[True] | None = None
    version: int | None = None


class DraftNotes(Answer):
    inline: int
    bcc_dropped: int
    plain: bool


class DraftOpened(Answer):
    ok: Literal[True]
    draft_id: int
    version: int
    mode: str
    account: str
    subject: str
    to: list[str]
    cc: list[str]
    bcc: list[str]
    text: str
    tail_text: str
    in_reply_to: str
    references: str
    orig_pk: int | None
    participants: list[str]
    files: list[DraftFile]
    split: str
    notes: DraftNotes
