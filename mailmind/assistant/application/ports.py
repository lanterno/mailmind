"""The Assistant's ports: one call to a language model, what comes back, how it fails, and the
ledger that counts what each call cost.

A LanguageModel sends a Prompt as one user turn and returns the Reply. When no reply comes it
raises ModelError, a RuntimeError, so the legacy callers' "a raise is retried later" contract
holds: the API rejected the call (`status` says how), or nothing answered (`status` is None).
Some say nothing was sent (NotSent): AIOff, which a model raises when there is no key, and what
the cost governor raises when it holds a call back (OverLimit, Deferred).
"""
from dataclasses import dataclass
from typing import Protocol

# The statuses worth sending again: a timeout, a conflict, a rate limit, an overload, a server
# error. Both adapters retry these, twice; any other 4xx says the request itself won't do.
RETRIED = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


@dataclass(frozen=True)
class Prompt:
    """One user turn, ready to send."""
    text: str
    model: str
    max_tokens: int
    schema: dict | None = None       # structured output's form of the schema (domain.schema)
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

    def days(self, since: str) -> list[dict]: ...        # per day and feature, from `since` on


class Mail(Protocol):
    """What the Assistant reads of the mail, which belongs to the Mailbox and to Accounts."""
    def fetch_body(self, pk: int) -> dict | None: ...
    def pk_now(self, c, key: str, folder: str) -> int | None: ...
    def accounts(self) -> list[dict]: ...
    def get_message(self, c, pk: int): ...
    def pk_in(self, c, key: str, folder: str) -> int | None: ...
    def do_trash(self, one: dict) -> dict: ...
    def do_write(self, path: str, one: dict) -> dict: ...
    def snooze(self, body: dict) -> tuple[dict, int]: ...


class Search(Protocol):
    """How a bulk command's filter is read and what it reaches, which belongs to Discovery."""
    def parse(self, text: str): ...
    def reach(self, spec, cap: int, apart: set[str], sample: int) -> tuple: ...
    def sweeps_whole_mailbox(self, matched: int) -> bool: ...
