"""The Assistant's ports: one call to a language model, what comes back, how it fails, and the
ledger that counts what each call cost.

A LanguageModel sends a Prompt as one user turn and returns the Reply. When no reply comes it
raises ModelError, a RuntimeError, so the legacy callers' "a raise is retried later" contract
holds: the API rejected the call (`status` says how), or nothing answered (`status` is None).
AIOff is the one a model raises without sending anything, when there is no key.
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


class AIOff(ModelError):
    """AI is off: there is no API key, so nothing was sent."""


class LanguageModel(Protocol):
    enabled: bool                    # False only for the stand-in used when AI is off

    def complete(self, prompt: Prompt) -> Reply: ...


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


class UsageLedger(Protocol):
    def record(self, spend: Spend) -> None: ...

    def spent(self, day: str) -> int: ...                # micro-dollars, that day

    def days(self, since: str) -> list[dict]: ...        # per day and feature, from `since` on
