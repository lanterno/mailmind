"""One sender's cleanup as a value: what it blocks, how far its sweep goes and what its moves came to. The block and the
sweep can be undone, so they stand whatever comes of the unsubscribe, which can't be and wants a preview's token."""
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from mailmind.curation.domain import rulebook
from mailmind.kernel.parsed import is_list

SWEEP_CAP = 200          # bound the per-sender IMAP-move burst; report if capped
BATCH_MAX = 40           # senders one batch may name: what the list of them shows
BATCH_SWEEP_CAP = 500    # moves one batch makes, in all: its undo group is rewritten with each, and a click waits on it
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


def senders_named(raw: object) -> list[str] | None:
    """The distinct senders a batch names, as rules and rows go by them, or None if it names none, too many, or anything that
    isn't an address."""
    if not is_list(raw) or not raw or len(raw) > BATCH_MAX:
        return None
    named = [sender_named(one) if isinstance(one, str) else None for one in raw]
    return None if None in named else list(dict.fromkeys(a for a in named if a))


def within_batch[T](read: Iterable[tuple[str, Sequence[T], bool]]) -> tuple[list[tuple[str, Sequence[T]]], bool]:
    """(what a batch sweeps of each sender, whether anything was left behind), from each sender's own `within_cap`. The moves are
    shared out in the order the senders came."""
    left = BATCH_SWEEP_CAP
    taken: list[tuple[str, Sequence[T]]] = []
    capped = False
    for addr, targets, hit_own_cap in read:
        kept = targets[:left]
        capped = capped or hit_own_cap or len(kept) < len(targets)
        left -= len(kept)
        taken.append((addr, kept))
    return taken, capped


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
