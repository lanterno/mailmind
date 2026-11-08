"""A weekly review, with no I/O: its shapes, what is taken from a model's answer, what stands in when the answer won't do, and
what makes one stale."""
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, TypedDict

from mailmind.assistant.domain import fingerprint


class Stats(TypedDict):
    sent: int
    received: int
    by_bucket: dict[str, int]
    needs_you: int
    waiting_on: int
    reply_later: int
    cleaned: int


class Needed(TypedDict):
    """A message that still needs you."""
    key: int | None
    folder: str
    account: str
    thread_id: str | None
    who: str
    subject: str
    importance: int
    action: str


class Awaited(TypedDict):
    """A thread you're waiting on. One whose thread has no row names no message."""
    account: str
    thread_id: str
    key: int | None
    folder: str | None
    subject: str
    to: str
    sent_ts: int


class Retrospective(TypedDict):
    narrative: str
    next: list[str]


def fingerprint_of(stats: Stats, needs: Sequence[Mapping[str, Any]], waiting: Sequence[Mapping[str, Any]]) -> str:
    """The week as reviewed: its counts, and the threads that need you or wait on others, named by
    account and thread so a renumbered folder changes nothing."""
    def threads(items: Iterable[Mapping[str, Any]]) -> str:
        return fingerprint.of(f'{i["account"]}|{i["thread_id"]}' for i in items)
    counts = "%d:%d:%d:%d" % (stats["sent"], stats["received"], stats["needs_you"], stats["waiting_on"])
    return f"{counts}|{threads(needs)}|{threads(waiting)}"


def fallback(stats: Stats, needs: Sequence[Needed], waiting: Sequence[Awaited]) -> Retrospective:
    n = (f"In the last 7 days you sent {stats['sent']} message"
         + ("s" if stats["sent"] != 1 else "")
         + f" and {stats['received']} came in. "
         + (f"{stats['needs_you']} still need a reply from you"
            + (f" and you're waiting to hear back on {stats['waiting_on']}." if stats["waiting_on"]
               else ".") if (stats["needs_you"] or stats["waiting_on"])
            else "Your inbox is clear — nothing is waiting on you."))
    nxt = [f"Reply to {x['who']} — {x['subject']}" for x in needs[:3]]
    return {"narrative": n, "next": nxt}


def clean(obj: Mapping[str, Any]) -> Retrospective:
    narrative = str(obj.get("narrative") or "")[:1400]
    told: list[Any] = obj.get("next") or []
    nxt = [str(x)[:160] for x in told if isinstance(x, str)][:3]
    return {"narrative": narrative, "next": nxt}


def degenerate(narrative: str) -> bool:
    """An empty reply, or a trivial stub like "test": a real retrospective is a few sentences."""
    return len(narrative) < 40 or len(narrative.split()) < 7
