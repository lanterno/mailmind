"""A weekly review, with no I/O: its shapes, what is taken from a model's answer, what stands in when the answer won't do, what
makes one stale, and when one is written ahead and read."""
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any, TypedDict

from mailmind.assistant.domain import brief, fingerprint

EVENTS_READ = 40            # of each list asked of the calendar
EVENTS_EACH_WAY = 6         # how many just gone, and how many to come
EVENT_SUMMARY_MAX = 120
AHEAD_DAYS = 7
WRITTEN_AHEAD_ON = 0        # Monday: the week just ended is reviewed on its morning, and read until the next


class Stats(TypedDict):
    sent: int
    received: int
    by_bucket: dict[str, int]
    needs_you: int
    waiting_on: int
    reply_later: int
    cleaned: int
    handled: int                    # what went to Archive and stayed there


class Meeting(TypedDict):
    """An event in the week's invites. All day usually means a deadline."""
    key: int | None
    folder: str
    uid: int | None
    account: str
    thread_id: str | None
    summary: str
    start: str                      # local, as the page reads it
    all_day: bool
    past: bool


class Needed(TypedDict):
    """A message that still needs you."""
    key: int | None
    folder: str
    uid: int | None                 # the copy's on the server, which tells two in one folder apart
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
    uid: int | None
    subject: str
    to: str
    sent_ts: int


class Retrospective(TypedDict):
    narrative: str
    next: list[str]


def is_ahead_morning(local: datetime) -> bool:
    """Whether the week's review may be written ahead at this local time: the morning brief's hours, on the week's first day."""
    return local.weekday() == WRITTEN_AHEAD_ON and brief.is_morning(local.hour)


def week_so_far(today: date) -> list[str]:
    """The days of today's week so far, newest first, from today back to its first: those a review written ahead is read on."""
    return [(today - timedelta(days=n)).isoformat() for n in range((today.weekday() - WRITTEN_AHEAD_ON) % 7 + 1)]


def meetings_of(gone: Iterable[Mapping[str, Any]], coming: Iterable[Mapping[str, Any]],
                since: datetime, now: datetime, until: datetime) -> list[Meeting]:
    """The week's events, from the calendar's answer as of a week ago and its answer as of now, since each lists what hadn't
    ended by then: the latest of what has started, then the soonest of what hasn't, each event once."""
    def iso(when: datetime) -> str:
        return when.isoformat(timespec="minutes")
    start, then, end = iso(since), iso(now), iso(until)
    each: dict[tuple[str, str], Mapping[str, Any]] = {}
    for event in (*gone, *coming):
        each.setdefault((event["summary"], event["start"]), event)
    within = sorted((e for e in each.values() if start <= e["start"] < end), key=lambda e: e["start"])
    chosen = [e for e in within if e["start"] < then][-EVENTS_EACH_WAY:] + [e for e in within if e["start"] >= then][:EVENTS_EACH_WAY]
    return [{"key": e["key"], "folder": e["folder"], "uid": e["uid"], "account": e["account"], "thread_id": e["thread_id"],
             "summary": str(e["summary"] or "(no title)")[:EVENT_SUMMARY_MAX], "start": e["start"], "all_day": bool(e["all_day"]),
             "past": e["start"] < then} for e in chosen]


def meeting_lines(meetings: Sequence[Meeting]) -> str:
    """The week's events as the model is told them."""
    return "\n".join(f'- {m["start"][:10] if m["all_day"] else m["start"].replace("T", " ")}'
                     f'{" (all day)" if m["all_day"] else ""}{" (already happened)" if m["past"] else ""}: {m["summary"]}'
                     for m in meetings) or "(none)"


def fingerprint_of(stats: Stats, needs: Sequence[Mapping[str, Any]], waiting: Sequence[Mapping[str, Any]]) -> str:
    """The week as reviewed: its counts, and the threads that need you or wait on others, named by
    account and thread so a renumbered folder changes nothing."""
    def threads(items: Iterable[Mapping[str, Any]]) -> str:
        return fingerprint.of(f'{i["account"]}|{i["thread_id"]}' for i in items)
    counts = "%d:%d:%d:%d:%d" % (stats["sent"], stats["received"], stats["needs_you"], stats["waiting_on"], stats["handled"])
    return f"{counts}|{threads(needs)}|{threads(waiting)}"


def fallback(stats: Stats, needs: Sequence[Needed], waiting: Sequence[Awaited]) -> Retrospective:
    n = (f"In the last 7 days you sent {stats['sent']} message"
         + ("s" if stats["sent"] != 1 else "")
         + f" and {stats['received']} came in. "
         + (f"You filed away {stats['handled']}. " if stats["handled"] else "")
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
