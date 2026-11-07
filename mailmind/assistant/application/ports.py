"""The Assistant's ports: one call to a language model, what comes back, how it fails, and the
ledger that counts what each call cost.

A LanguageModel sends a Prompt as one user turn and returns the Reply. When no reply comes it
raises ModelError, a RuntimeError, so the legacy callers' "a raise is retried later" contract
holds: the API rejected the call (`status` says how), or nothing answered (`status` is None).
Some say nothing was sent (NotSent): AIOff, which a model raises when there is no key, and what
the cost governor raises when it holds a call back (OverLimit, Deferred).
"""
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict

from mailmind.content.mime import Parsed
from mailmind.kernel.row import Connection, Row

# The statuses worth sending again: a timeout, a conflict, a rate limit, an overload, a server
# error. Both adapters retry these, twice; any other 4xx says the request itself won't do.
RETRIED = frozenset({408, 409, 429, 500, 502, 503, 504, 529})

type Connect = Callable[[], Connection]
type Result = tuple[dict[str, Any], int]      # what a request is answered with: its JSON, and the HTTP status


@dataclass(frozen=True)
class Prompt:
    """One user turn, ready to send."""
    text: str
    model: str
    max_tokens: int
    schema: dict[str, Any] | None = None       # structured output's form of the schema (domain.schema)
    effort: str | None = None        # None: the model takes no effort setting
    fallback: bool = False           # ask for the server-side refusal fallback


@dataclass(frozen=True)
class Usage:
    """The tokens a reply was billed for."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0


@dataclass(frozen=True)
class Reply:
    """What came back: the answer's text ("" for a refusal), the model that actually served it
    (another one, after a server-side fallback), its usage and why it stopped."""
    text: str
    model: str
    usage: Usage
    stop_reason: str

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"


@dataclass(frozen=True)
class BatchResult:
    """What became of one request in a batch: its reply, or None with `permanent` saying that sending it
    again won't change that (a bad request, not an outage, a cancel or an expiry)."""
    custom_id: str
    reply: Reply | None
    permanent: bool = False


class ModelError(RuntimeError):
    """No reply: the API rejected the call with `status`, or nothing answered (status None)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def permanent(self) -> bool:
        """A rejection that sending the same thing again won't change: a bad request, key or
        model, not a rate limit or an outage."""
        return self.status is not None and 400 <= self.status < 500 and self.status not in RETRIED


class NotSent(ModelError):
    """Nothing was sent, so no call was made: not a failure, and the ledger counts nothing."""


class AIOff(NotSent):
    """AI is off: there is no API key, so nothing was sent."""


class HeldBack(NotSent):
    """The cost governor held the call back. Callers that turn a failure into "try again later"
    let this through, so the route or the job can say why."""


class OverLimit(HeldBack):
    """An interactive call at the daily AI spending limit, which the user hasn't said may go over.
    The page asks, then sends the request again with leave to."""

    def __init__(self, spent_micros: int, limit_micros: int):
        super().__init__("today's AI spending has reached the daily limit")
        self.spent_micros = spent_micros
        self.limit_micros = limit_micros


class Deferred(HeldBack):
    """Background or speculative work that may not start now. `reason` says why: "yielding" (the
    user is using AI), "away" (nobody has used the app lately), "limit" (today's spending reached
    the daily limit) or "breaker" (the API keeps rejecting these calls)."""

    def __init__(self, reason: str):
        super().__init__(f"held back: {reason}")
        self.reason = reason


class LanguageModel(Protocol):
    enabled: bool                    # False only for the stand-in used when AI is off

    def complete(self, prompt: Prompt) -> Reply: ...

    def submit_batch(self, requests: list[tuple[str, Prompt]]) -> str: ...     # the batch's id; never retried

    def batch_ended(self, batch_id: str) -> bool: ...

    def batch_results(self, batch_id: str, asked: str) -> list[BatchResult]: ...

    def discard_batch(self, batch_id: str) -> None: ...      # removes what the API keeps of it


@dataclass(frozen=True)
class Spend:
    """One call, as the usage ledger counts it."""
    day: str                         # the local date it counts against, YYYY-MM-DD
    feature: str                     # what made it: triage, thread, brief, draft, ...
    model: str                       # the model asked for
    served: str                      # the model that served it; "" when no reply came
    outcome: str                     # ok, refused, rejected (a permanent 4xx) or failed
    usage: Usage
    cost_micros: int                 # US dollars × 1,000,000
    ts: int                          # when it ended, in seconds since the epoch
    calls: int = 1                   # more than one for the answers of a batch, counted together


class UsageLedger(Protocol):
    def record(self, spend: Spend) -> None: ...

    def spent(self, day: str) -> int: ...                # micro-dollars, that day

    def reserved(self) -> int: ...                       # what batches still out may cost, in micro-dollars

    def days(self, since: str) -> list[dict[str, Any]]: ...   # per day and feature, from `since` on


class Mail(Protocol):
    """What the Assistant reads of the mail, which belongs to the Mailbox, Discovery and Accounts. Every port it declares only reads: a
    plan's confirm runs through the mail's own contexts."""
    def fetch_body(self, pk: int) -> Parsed | None: ...
    def pk_now(self, c: Connection, key: int, folder: str) -> int | None: ...
    def accounts(self) -> list[dict[str, Any]]: ...
    def get_message(self, c: Connection, pk: int) -> Row | None: ...
    def for_asking(self, match: str, accounts: str | list[str], limit: int) -> list[Row] | None: ...
    def brief_rows(self, c: Connection, accounts: Collection[str], n: int) -> list[Row]: ...
    def delta_rows(self, c: Connection, accounts: Collection[str], since_ts: int, n: int) -> list[Row]: ...


class Filter(Protocol):
    """A bulk command's filter, read by Discovery's grammar."""
    def narrows(self) -> bool: ...
    def names_folder(self) -> bool: ...


class Search(Protocol):
    """How a bulk command's filter is read and what it reaches, which belongs to Discovery."""
    def parse(self, text: str) -> Filter: ...
    def reach(self, spec: Filter, cap: int, apart: set[str],
              sample: int) -> tuple[int | None, list[tuple[int, str]], list[dict[str, str]], int]: ...
    def sweeps_whole_mailbox(self, matched: int) -> bool: ...


class BatchStatus(TypedDict):
    """What's out in a batch."""
    waiting: int
    state: str | None
    estimate_micros: int


class Answers(Protocol):
    """What the AI said and what it was asked, each over its caller's connection. See adapters.sqlite."""
    def get_thread_summary(self, c: Connection, account: str, thread_id: str, scope: str = "thread") -> Row | None: ...
    def save_thread_summary(self, c: Connection, row: Mapping[str, Any]) -> None: ...

    def get_brief(self, c: Connection, accounts: str, day: str) -> Row | None: ...
    def save_brief(self, c: Connection, accounts: str, day: str, fingerprint: str, narrative: str, groups_json: str,
                   model: str, created_at: str) -> None: ...
    def get_delta(self, c: Connection, accounts: str, since_ts: int, fingerprint: str) -> Row | None: ...
    def save_delta(self, c: Connection, accounts: str, since_ts: int, fingerprint: str, headline: str, items_json: str,
                   model: str, created_at: str) -> None: ...
    def get_review(self, c: Connection, accounts: str, day: str) -> Row | None: ...
    def save_review(self, c: Connection, accounts: str, day: str, fingerprint: str, narrative: str, next_json: str,
                    stats_json: str, needs_json: str, waiting_json: str, model: str, created_at: str) -> None: ...

    def draft_ahead_get(self, c: Connection, account: str, ref_key: str) -> Row | None: ...
    def draft_ahead_put(self, c: Connection, account: str, ref_key: str, fp: str, body: str, tone: str | None,
                        model: str | None, src_pk: int, src_mid: str | None, source: str, now: int) -> None: ...
    def draft_ahead_fail(self, c: Connection, account: str, ref_key: str, now: int) -> None: ...
    def draft_ahead_touch(self, c: Connection, account: str, ref_key: str, now: int) -> None: ...
    def draft_ahead_gc(self, c: Connection, now: int, orphans: bool = True) -> None: ...

    def needs_summary(self, c: Connection, limit: int = 200, newer_than: int | None = None) -> list[Row]: ...
    def save_summary(self, c: Connection, pk: int, summary: str, importance: int, category: str, action: str,
                     model: str, created_at: str) -> None: ...
    def save_assessment(self, c: Connection, key: int, summary: str, importance: int, category: str, action: str,
                        model: str, created_at: str) -> None: ...

    def batch_open(self, c: Connection) -> Row | None: ...
    def batch_begin(self, c: Connection, feature: str, model: str, keys: Collection[int], estimate_micros: int,
                    cutoff_ts: int, now: int) -> int | None: ...
    def batch_sent(self, c: Connection, batch_id: int, remote_id: str, now: int) -> None: ...
    def batch_drop(self, c: Connection, batch_id: int) -> None: ...
    def batch_polled(self, c: Connection, batch_id: int, now: int) -> None: ...
    def batch_keys(self, c: Connection, batch_id: int) -> set[int]: ...
    def batch_collected(self, c: Connection, batch_id: int, now: int) -> None: ...
    def batch_discarded(self, c: Connection, batch_id: int) -> None: ...
    def batches_in_state(self, c: Connection, state: str) -> list[Row]: ...
    def batch_waiting(self, c: Connection) -> int: ...
