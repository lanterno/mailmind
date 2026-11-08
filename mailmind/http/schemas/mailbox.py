"""What the page asks of the Mailbox, and what it answers."""
from typing import Literal

from mailmind.http.schemas.common import Answer, Failure, Flag, Number, QueryArgs, RequestBody

# ── what a request says ──────────────────────────────────────────────────────────────────────────────────────────────


class ListArgs(QueryArgs):
    offset: Number = 0
    limit: Number = 80
    group: str | None = None                # "thread" asks for one row a conversation


class FolderListArgs(ListArgs):
    account: str | None = None
    folder: str = "INBOX"


class FoldersArgs(QueryArgs):
    account: str | None = None              # left out is the route's `account required`


class ThreadArgs(QueryArgs):
    account: str | None = None              # either left out is the use case's `missing account/thread`
    thread_id: str | None = None


class BodyArgs(QueryArgs):
    prefetch: str | None = None             # "1" warms the store and shows nothing


class BodyDocumentArgs(QueryArgs):
    """Text and nothing but, so nothing here is ever refused: a frame shows what it's sent, and a JSON 400 isn't a document. The
    use case asks whether `key` is this row's."""
    key: str = ""
    images: str = "0"


class NamesAPending(RequestBody):
    id: Number                              # going without one is already `bad request`


class MoveBody(RequestBody):
    pk: Number | None = None                # left out is each use case's own: `bad pk`, or for a trash `bad request`
    undo_group: str | None = None
    mutation_id: str | None = None


class FlagBody(RequestBody):
    pk: Number | None = None
    flag: str | None = None                 # left out it's `flagged`; one of no use is the use case's `unknown flag`
    on: Flag | None = None                  # left out, it sets
    mutation_id: str | None = None


class SeenBody(RequestBody):
    pk: Number | None = None
    seen: Flag | None = None                # left out, it marks read
    mutation_id: str | None = None


class BulkRow(RequestBody):
    pk: Number | None = None
    whole: Flag | None = None               # the row stands for its conversation in its folder


class BulkFileBody(RequestBody):
    verb: str | None = None
    rows: list[BulkRow] | None = None
    undo_group: str | None = None
    mutation_id: str | None = None


class ReconcileBody(RequestBody):
    account: str | None = "all"
    dry_run: Flag | None = False
    force: Flag | None = False


class SyncRetryBody(RequestBody):
    account: str | None = None


# ── a message in a list ──────────────────────────────────────────────────────────────────────────────────────────────


class MessageRow(Answer):
    pk: int
    key: int
    from_name: str | None
    from_addr: str | None
    subject: str
    when: str
    snippet: str
    seen: bool
    flagged: bool
    has_attach: bool
    is_junk: bool


class _Verdict(Answer):
    """What the AI said of a message, and what that asks of you (`kernel.attention`)."""
    importance: int | None
    needs_you: bool
    reply_worthy: bool
    commitment_worthy: bool
    safe_to_file: bool


class _Listed(MessageRow):
    bucket: str | None
    thread_id: str | None


class FolderRow(_Listed, _Verdict):
    summary: str
    action: str
    category: str | None
    ts: int | None
    account: str
    count: int | None = None                # one row a conversation: how many it stands for


class SnoozedRow(_Listed, _Verdict):
    folder: str
    snoozed_until: int


class _ThreadRow(_Listed):
    """One row a thread, its latest message."""
    folder: str
    ts: int | None
    count: int


class MutedRow(_ThreadRow):
    muted: Literal[True]


class ReplyLaterRow(_ThreadRow):
    replylater: Literal[True]
    waiting_label: str


class WaitingRow(_ThreadRow):
    waiting: Literal[True]
    waiting_label: str
    kind: str
    to: str


class WaitingOnOthersRow(WaitingRow):
    account: str


class ThreadMessage(MessageRow):
    folder: str
    answered: bool


# ── what the lists answer ────────────────────────────────────────────────────────────────────────────────────────────


class _Page(Answer):
    ok: Literal[True]
    folder: str
    total: int
    offset: int
    has_more: bool


class _OfAnAccount(_Page):
    account: str | None


class FolderListing(_OfAnAccount):
    items: list[FolderRow]
    grouped: bool
    syncing: Literal[True] | None = None
    sync_failed: Literal[True] | None = None
    catching_up: Literal[True] | None = None


class SnoozedListing(_OfAnAccount):
    items: list[SnoozedRow]
    snoozed: Literal[True]


class MutedListing(_OfAnAccount):
    items: list[MutedRow]
    muted: Literal[True]


class WaitingListing(_OfAnAccount):
    items: list[WaitingRow]
    waiting: Literal[True]


class ReplyLaterListing(_OfAnAccount):
    items: list[ReplyLaterRow]
    replylater: Literal[True]


class InboxesListing(_Page):
    items: list[FolderRow]
    grouped: bool
    syncing: Literal[True] | None = None


class WaitingOnOthers(Answer):
    ok: Literal[True]
    items: list[WaitingOnOthersRow]


class Thread(Answer):
    ok: Literal[True]
    account: str
    thread_id: str
    items: list[ThreadMessage]
    count: int
    reply_pk: int | None


class RailFolder(Answer):
    name: str
    role: str
    unseen: int
    total: int


class Folders(Answer):
    ok: Literal[True]
    account: str
    folders: list[RailFolder]
    snoozed: int
    muted: int
    waiting: int
    replylater: int


class FoldersRefused(Failure):
    folders: list[RailFolder] | None = None


class PendingChange(Answer):
    id: int
    account: str
    op: str
    folder: str
    dest: str | None
    flags: dict[str, bool] | None
    state: str
    attempts: int
    error: str | None
    subject: str | None
    next_attempt_at: int
    created_at: str


class Pending(Answer):
    items: list[PendingChange]
    waiting: int
    stuck: int


# ── what the page opens on ───────────────────────────────────────────────────────────────────────────────────────────


class DigestItem(_Verdict):
    pk: int
    key: int
    account: str
    kind: str
    alias: str | None
    from_name: str | None
    from_addr: str | None
    subject: str
    when: str
    summary: str | None
    category: str | None
    action: str
    link: str
    bucket: str | None
    seen: bool
    flagged: bool
    has_attach: bool
    thread_id: str | None
    folder: str
    ts: int | None


class DigestStats(Answer):
    total: int
    kept: int
    summarized: int
    verified: int
    needs: int


class AccountSync(Answer):
    health: str
    msg: str


class SyncState(Answer):
    running: bool
    last: str | None
    result: str


class AiStatus(Answer):
    """`Status.report`: how AI stands, with no call made."""
    engine: str
    model: str
    key_from: str
    last_ok: bool | None
    last_ts: int
    last_error: str | None
    spent_today_micros: int | None
    limit_micros: int
    batch_waiting: int | None = None


class Digest(Answer):
    items: list[DigestItem]
    stats: DigestStats
    accounts: list[str]
    send_accounts: list[str]
    aliases: dict[str, str | None]
    signatures: dict[str, str]
    signature_default: str
    status: dict[str, AccountSync]
    sync: SyncState
    ai: AiStatus
    profile: str
    generated: str


class AccountStats(Answer):
    account: str
    kind: str
    alias: str | None
    in_inbox: int
    by_bucket: dict[str, int]
    junk: int
    keep: int
    top_senders: dict[str, list[str]]
    error: str | None


class Bucket(Answer):
    name: str
    is_junk: bool


class Totals(Answer):
    in_inbox: int
    junk: int
    keep: int


class Stats(Answer):
    accounts: list[AccountStats]
    buckets: list[Bucket]
    totals: Totals
    generated: str


# ── a message opened ─────────────────────────────────────────────────────────────────────────────────────────────────


class AttachmentPart(Answer):
    part_id: str
    filename: str
    content_type: str
    size: int
    content_id: str | None
    is_inline: int


class Party(Answer):
    """An organizer or an attendee. An invite with no organizer holds an empty one."""
    name: str | None = None
    email: str | None = None


class Invite(Answer):
    summary: str
    location: str
    description: str
    when: str
    all_day: bool
    start_iso: str
    end_iso: str
    organizer: Party
    attendees: list[Party]
    recurrence: str
    method: str
    status: str
    uid: str
    rrule: str
    exdates: list[str]
    recurrence_id: str
    tzid: str
    rdates: list[str] | None = None         # only where the text has some


class MessageOpened(Answer):
    ok: Literal[True]
    pk: int
    key: int
    html: str | None
    text: str | None
    has_html: bool
    attachments: list[AttachmentPart]
    invite: Invite | None
    invite_part: str | None
    blocked_images: int
    blocked_css_urls: int
    to: str
    cc: str
    date: str
    reply_to: str
    from_name: str | None
    from_addr: str | None
    subject: str | None
    account: str
    thread_id: str | None
    message_id: str | None
    in_reply_to: str | None
    references: str | None
    draft_id: int | None = None             # these three only for a draft
    draft_mode: str | None = None
    is_draft: Literal[True] | None = None


class Prefetched(Answer):
    ok: Literal[True]
    prefetched: bool                        # false: it gave way to another job on the account's gate


# ── filing ───────────────────────────────────────────────────────────────────────────────────────────────────────────


class Filed(Answer):
    ok: Literal[True]
    msg: str
    undoable: bool
    status: str | None = None               # `queued` or `waiting`: the worker has it


class ManyFiled(Answer):
    ok: Literal[True]                       # some of them were: none is a 404 or a 409
    msg: str
    pks: list[int]
    count: int
    failed: int
    queued: int
    undoable: bool


class RowsFiled(ManyFiled):
    rows_failed: int
    partial: bool


class Undone(Answer):
    ok: Literal[True]
    label: str | None
    account: str | None
    folder: str | None
    remaining: int
    reversed: int | None = None             # these two only for a group
    failed: int | None = None


class NothingToUndo(Failure):
    """The stack is empty: any other 404 says what `Failure` says."""
    empty: Literal[True] | None = None


class Done(Answer):
    ok: Literal[True]


# ── the sync ─────────────────────────────────────────────────────────────────────────────────────────────────────────


class SyncStarted(Answer):
    ok: Literal[True]
    msg: str


class SyncRetried(Answer):
    ok: Literal[True]
    account: str


class AccountReconciled(Answer):
    account: str
    live: int
    stored: int
    ghosts: int
    pruned: int
    error: str | None
    folders: int


class Reconciled(Answer):
    ok: Literal[True]
    dry_run: bool
    results: list[AccountReconciled]
    total_ghosts: int
    total_pruned: int
    generated: str
