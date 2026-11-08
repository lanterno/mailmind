"""What the page asks of Curation, and what it answers."""
from typing import Literal

from pydantic import Field

from mailmind.curation.api import BlockScope, FilterVerb, RuleChange
from mailmind.http.schemas.common import Answer, Flag, NamesAMessage, Number, QueryArgs, RequestBody


class RuleBody(NamesAMessage):
    """A block trashes the copy `key` and `folder` name too, if it is still there."""
    token: str | None = None
    verdict: RuleChange | None = None       # left out is the use case's `verdict must be keep|block|remove`


class FilterChange(RequestBody):
    verb: FilterVerb | None = None          # left out is the use case's `verb must be create|update|delete`
    id: str | None = None
    q: str | None = None
    star: Flag | None = None
    read: Flag | None = None


class CleanupBody(RequestBody):
    from_addr: str | None = None
    block_scope: BlockScope | None = None   # left out is the sender
    do_unsubscribe: Flag | None = None
    confirm_token: str | None = None        # the preview's, as it gave it
    undo_group: str | None = None


class SweepBody(RequestBody):
    account: str | None = "all"
    dry_run: Flag | None = None


class AuditArgs(QueryArgs):
    limit: Number = 120


class ScreenerArgs(QueryArgs):
    account: str | None = None


class SenderArgs(QueryArgs):
    addr: str | None = None


class MailFilter(Answer):
    """What to do with new mail that fits a search."""
    id: str
    q: str
    star: bool
    read: bool


class Rules(Answer):
    keep_senders: list[str]
    block_senders: list[str]
    keep_domains: list[str]
    block_domains: list[str]
    expire_hours: dict[str, int]
    filters: list[MailFilter]


class RuleBook(Answer):
    rules: Rules


class Filters(Answer):
    ok: Literal[True]
    filters: list[MailFilter]


class RuleChanged(Answer):
    ok: Literal[True]
    token: str
    placed_in: str | None                   # a removal places it nowhere
    reclassified: int
    trashed: bool
    rules: Rules


class AuditEvent(Answer):
    """A line of an audit trail: a sweep's message, a sender's cleanup or a reconcile's count."""
    kind: str
    ts: str
    account: str | None = None
    uid: str | None = None
    sender: str | None = Field(None, alias="from")
    subject: str | None = None
    bucket: str | None = None
    reason: str | None = None
    token: str | None = None
    swept: int | None = None
    status: str | None = None
    auto: bool | None = None
    pruned: int | None = None
    live: int | None = None


class AuditTrail(Answer):
    events: list[AuditEvent]
    has_more: bool


class ScreenerItem(Answer):
    from_addr: str
    from_name: str
    domain: str
    count: int
    when: str


class ScreenerQueue(Answer):
    ok: Literal[True]
    account: str | None
    items: list[ScreenerItem]
    total: int


class SenderAccount(Answer):
    email: str
    count: int


class NoisySender(Answer):
    from_addr: str
    from_name: str
    domain: str
    count: int
    unseen: int
    unseen_ratio: float
    last_seen: str
    bucket: str
    lu_likely: bool
    noise_score: float
    accounts: list[SenderAccount]


class NoisySenders(Answer):
    ok: Literal[True]
    generated: str
    senders: list[NoisySender]


class WayOut(Answer):
    """What a person is shown of a sender's way out before confirming: the way, and only where it goes."""
    method: str
    https_host: str | None = None
    mailto_to: str | None = None


class Reversible(Answer):
    block: bool
    sweep: bool
    unsubscribe: bool


class SenderPreview(Answer):
    ok: Literal[True]
    from_addr: str
    count: int
    unseen: int
    accounts: list[str]
    domain: str
    unsub: WayOut
    sweep_count: int
    sweep_capped: bool
    reversible: Reversible
    confirm_token: str


class CleanedUp(Answer):
    ok: Literal[True]
    status: str
    method: str
    blocked_token: str
    block_scope: BlockScope
    swept: int
    sweep_failed: int
    sweep_queued: int
    sweep_capped: bool
    browser_url: str | None
    http_status: int | None
    undo_group: str
    reversible: Reversible


class AccountSwept(Answer):
    """One account's sweep. One that failed on the way in says only how far it got."""
    account: str
    planned: int
    error: str | None
    by_bucket: dict[str, int] | None = None
    senders: int | None = None
    moved: int | None = None
    stragglers: list[str] | None = None


class Swept(Answer):
    ok: Literal[True]
    dry_run: bool
    results: list[AccountSwept]
    generated: str
