"""What the page asks of the Assistant, and what it answers."""
from typing import Literal

from pydantic import Field

from mailmind.assistant.api import DraftTone, PlanVerb, RewriteOp
from mailmind.http.schemas.common import Answer, Copy, Failure, Flag, NamesAMessage, Number, QueryArgs, RequestBody
from mailmind.kernel.sender_verdict import Verdict
from mailmind.kernel.snoozed import SnoozePreset


class SummarizeBody(NamesAMessage):
    """A message by its key, or a thread by its account and id."""
    account: str | None = None
    thread_id: str | None = None
    force: Flag | None = None


class DraftReplyBody(NamesAMessage):
    """What to reply to as a summary names it, and `account` says who replies, which may not be whose mail it is."""
    account: str | None = None
    thread_id: str | None = None
    tone: DraftTone | None = None
    instruction: str | None = None


class RewriteBody(RequestBody):
    text: str | None = None
    op: RewriteOp | None = None             # left out is the use case's `unknown op`
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
    key: Number                             # a message, the one at `uid` in `folder` where it has a copy there
    folder: str | None = None
    uid: Number | None = None
    peek: str | None = None


class SuggestedEvent(Answer):
    """A meeting the text proposes, its `start` and `end` a day or a time (`kernel.proposed`)."""
    title: str
    start: str
    end: str | None
    place: str
    all_day: bool


class SuggestedTask(Answer):
    title: str
    due: str | None                         # a day, YYYY-MM-DD


class Extracted(Answer):
    ok: Literal[True]
    events: list[SuggestedEvent]
    tasks: list[SuggestedTask]
    model: str


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
    handled: int | None = None      # none in a review stored before it counted that


class ReviewNeeded(Answer):
    """A message that still needs you, by its key and the folder and uid of its copy now."""
    key: int | None
    folder: str
    uid: int | None
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
    uid: int | None
    account: str
    thread_id: str
    subject: str
    to: str
    sent_ts: int


class RationaleBody(RequestBody):
    senders: list[str] | None = None        # left out is the use case's `bad senders`


class CleanupRationales(Answer):
    ok: Literal[True]
    rationales: dict[str, str]              # by sender


class SenderSummaryBody(RequestBody):
    addr: str | None = None                 # left out is the use case's `bad address`


class SenderSummary(Answer):
    ok: Literal[True]
    addr: str
    threads: int                            # how many of their threads it read
    summary: str
    topics: list[str]
    open: str


class ScreenerSuggestBody(RequestBody):
    account: str | None = None


class ScreenerSuggestion(Answer):
    """What the AI would do about a sender, surely enough to say so. A person still clicks: nothing writes a rule from this."""
    from_addr: str
    verdict: Verdict
    confidence: float
    why: str


class ScreenerSuggestions(Answer):
    ok: Literal[True]
    account: str | None
    asked: int                              # how many senders the pass read
    suggestions: list[ScreenerSuggestion]


class ReviewMeeting(Answer):
    """An event in the week's invites, by its message's key and the folder and uid of its copy now."""
    key: int | None
    folder: str
    uid: int | None
    account: str
    thread_id: str | None
    summary: str
    start: str
    all_day: bool
    past: bool


class Review(Answer):
    ok: Literal[True]
    narrative: str
    next: list[str]
    stats: ReviewStats
    needs: list[ReviewNeeded]
    waiting: list[ReviewAwaited]
    meetings: list[ReviewMeeting]
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
    op: RewriteOp


class AskSource(Answer):
    key: int
    folder: str
    uid: int
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
    verb: PlanVerb
    filter: str
    snooze_preset: SnoozePreset            # what a snooze waits for, and "tomorrow" for any other verb
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
    verb: PlanVerb
    count: int
    failed: int
    gone: int
    queued: int
    undoable: bool
