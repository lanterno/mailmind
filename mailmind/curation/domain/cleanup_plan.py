"""One sender's cleanup as a value: what it blocks, how far its sweep goes and what its moves came to. The block and the
sweep can be undone, so they stand whatever comes of the unsubscribe, which can't be and wants a preview's token."""
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from mailmind.curation.domain import rulebook

SWEEP_CAP = 200          # bound the per-sender IMAP-move burst; report if capped
type BlockScope = Literal["sender", "domain"]           # how far a block goes: a request's schema names it so
SENDER: Final[BlockScope] = "sender"
DOMAIN: Final[BlockScope] = "domain"
REVERSIBLE: Final[Mapping[str, bool]] = {"block": True, "sweep": True, "unsubscribe": False}


def sender_named(raw: str | None) -> str | None:
    """The sender a request names, as rules and rows go by it. None if that's no address."""
    addr = rulebook.token_of(raw or "")
    return addr if "@" in addr else None


def to_read() -> int:
    """How many of a sender's messages to read: one past the cap says whether the cap was hit."""
    return SWEEP_CAP + 1


def within_cap[T](read: Sequence[T]) -> tuple[Sequence[T], bool]:
    """What a sweep takes of `read`, the messages `to_read` asked for, and whether that leaves any behind."""
    return read[:SWEEP_CAP], len(read) > SWEEP_CAP


@dataclass(frozen=True)
class CleanupPlan:
    """What a person confirmed for one sender. `scope` says how far the block goes: this sender or their whole domain."""
    from_addr: str
    scope: BlockScope
    do_unsubscribe: bool
    confirm_token: str

    @property
    def block_token(self) -> str:
        return self.from_addr if self.scope == SENDER else rulebook.domain_of(self.from_addr)


def plan_from(body: Mapping[str, Any]) -> CleanupPlan | None:
    """A request as a plan, or None if it names no sender. Only asking for the domain widens a block."""
    from_addr = sender_named(body.get("from_addr"))
    if from_addr is None:
        return None
    return CleanupPlan(from_addr, DOMAIN if body.get("block_scope") == DOMAIN else SENDER,
                       bool(body.get("do_unsubscribe")), str(body.get("confirm_token") or ""))


@dataclass(frozen=True)
class SweepTally:
    """What filing each message came to. A queued one counts as swept: it has left the lists, and the worker has it."""
    moved: int
    queued: int
    failed: int

    @property
    def swept(self) -> int:
        return self.moved + self.queued


def tally(answers: Iterable[Mapping[str, Any]]) -> SweepTally:
    """Each answer filing gave, one a message. One that raised is an empty answer, and counts as failed."""
    moved = queued = failed = 0
    for answer in answers:
        if answer.get("status") == "queued":
            queued += 1
        elif answer.get("ok"):
            moved += 1
        else:
            failed += 1
    return SweepTally(moved, queued, failed)
