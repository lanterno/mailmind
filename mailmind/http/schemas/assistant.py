"""What the page asks of the Assistant, and what it answers."""
from typing import Literal

from pydantic import Field

from mailmind.http.schemas.common import Answer, Copy, Failure, Flag, NamesAMessage, Number, QueryArgs, RequestBody


class SummarizeBody(NamesAMessage):
    """A message by its key, or a thread by its account and id."""
    account: str | None = None
    thread_id: str | None = None
    force: Flag | None = None


class DraftReplyBody(NamesAMessage):
    """What to reply to as a summary names it, and `account` says who replies, which may not be whose mail it is."""
    account: str | None = None
    thread_id: str | None = None
    tone: str | None = None
    instruction: str | None = None


class RewriteBody(RequestBody):
    text: str | None = None
    op: str | None = None                   # unknown to the use case is its `unknown op`
    lang: str | None = None


class AskBody(RequestBody):
    account: str | None = None
    question: str | None = None


class PlanBody(RequestBody):
    cmd: str | None = None


class ConfirmBody(RequestBody):
    plan: str | None = None                 # a plan no one made has expired, as far as anyone is told
    undo_group: str | None = None


class DeltaBody(RequestBody):
    account: str | None = None
    since: Number | str | None = None       # seconds. What doesn't read as a time is the use case's to read as none


class DeltaArgs(QueryArgs):
    account: str | None = None
    since: str | None = None


class DraftAheadArgs(QueryArgs):
    key: Number                             # a message, the one in `folder` where it has a copy there
    folder: str | None = None
    peek: str | None = None


class Unavailable(Failure):
    """The model didn't answer (`Unreachable`), and `retry` says asking again can help."""
    retry: Literal[True] | None = None


class Expired(Failure):
    """A plan's confirm that came too late, or came twice."""
    expired: Literal[True]


class BriefGroup(Answer):
    label: str
    count: int
    messages: list[Copy]                    # each as its copy is now


class Brief(Answer):
    ok: Literal[True]
    narrative: str
    groups: list[BriefGroup]
    model: str
    generated: str
    cached: bool
    stale: bool


class BriefMissing(Answer):
    """None is stored yet, and a read writes none."""
    ok: Literal[True]
    missing: Literal[True]
    narrative: str
    groups: list[BriefGroup]
    cached: bool
    stale: bool


class DeltaItem(Answer):
    who: str
    gist: str
    importance: int


class Delta(Answer):
    ok: Literal[True]
    empty: Literal[False]
    headline: str
    items: list[DeltaItem]
    count: int
    cached: bool


class DeltaEmpty(Answer):
    """Nothing new since the time asked about."""
    ok: Literal[True]
    empty: Literal[True]
    count: int


class DeltaMissing(Answer):
    """There is news, none is stored of it yet, and a read writes none."""
    ok: Literal[True]
    empty: Literal[False]
    missing: Literal[True]
    count: int


class ReviewStats(Answer):
    sent: int
    received: int
    by_bucket: dict[str, int]
    needs_you: int
    waiting_on: int
    reply_later: int
    cleaned: int


class ReviewNeeded(Answer):
    """A message that still needs you, by its key and the folder its copy is in now."""
    key: int | None
    folder: str
    account: str
    thread_id: str | None
    who: str
    subject: str
    importance: int
    action: str


class ReviewAwaited(Answer):
    """A thread you're waiting on. One whose thread has no row names no message."""
    key: int | None
    folder: str | None
    account: str
    thread_id: str
    subject: str
    to: str
    sent_ts: int


class Review(Answer):
    ok: Literal[True]
    narrative: str
    next: list[str]
    stats: ReviewStats
    needs: list[ReviewNeeded]
    waiting: list[ReviewAwaited]
    week_start: str
    model: str
    generated: str
    cached: bool
    stale: bool


class ReviewMissing(Answer):
    """None is stored yet, and a read writes none."""
    ok: Literal[True]
    missing: Literal[True]


class Summary(Answer):
    ok: Literal[True]
    account: str
    thread_id: str
    scope: str
    cached: bool
    stale: bool
    tldr: str
    points: list[str]
    action: str
    participants: list[str]
    message_count: int
    model: str
    created_at: str


class DraftedReply(Answer):
    ok: Literal[True]
    body: str
    tone: str
    model: str


class DraftAheadStatus(Answer):
    ok: Literal[True]
    status: Literal["none", "generating"]


class DraftAheadReady(Answer):
    ok: Literal[True]
    status: Literal["ready"]
    body: str
    tone: str | None


class Rewritten(Answer):
    ok: Literal[True]
    text: str
    op: str


class AskSource(Answer):
    key: int
    folder: str
    sender: str | None = Field(alias="from")
    subject: str
    snippet: str
    account: str
    date: str


class Answered(Answer):
    ok: Literal[True]
    account: str
    question: str
    answer: str
    sources: list[AskSource]
    model: str
    generated: str


class PlanSample(Answer):
    sender: str = Field(alias="from")
    subject: str
    when: str


class PlanPreview(Answer):
    ok: Literal[True]
    intent: Literal["act"]
    verb: str
    filter: str
    snooze_preset: str
    count: int
    cap: int
    capped: bool
    plan: str
    sample: list[PlanSample]


class PlanIsASearch(Answer):
    """A command that isn't a clean, narrow action acts on nothing, and shows its filter instead."""
    ok: Literal[True]
    intent: Literal["search"]
    filter: str
    empty: Literal[True] | None = None


class PlanCarriedOut(Answer):
    ok: Literal[True]
    verb: str
    count: int
    failed: int
    gone: int
    queued: int
    undoable: bool
