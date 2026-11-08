"""What the model's reading of a message comes to: the meetings and the dated tasks its text proposes, as suggestions a person
looks over. Nothing here creates anything. The model's answer is read with care, within the caps, and an invalid date is
dropped, as `kernel.proposed` says. An event needs a day, so one with none goes; a task can stand without one, and the person
can give it one. Total: nothing here raises.
"""
import re
from collections.abc import Callable, Hashable, Iterable
from datetime import date
from typing import Any, TypedDict

from mailmind.kernel import proposed
from mailmind.kernel.parsed import is_list, is_object

MAX_EVENTS = 5
MAX_TASKS = 10
TEXT_MAX = 8000                         # how much of a message is read: a day named near the end of a long one is still found

_QUOTED = re.compile(r"^\s*>")


class SuggestedEvent(TypedDict):
    title: str
    start: str
    end: str | None
    place: str
    all_day: bool


class SuggestedTask(TypedDict):
    title: str
    due: str | None


class Suggestions(TypedDict):
    events: list[SuggestedEvent]
    tasks: list[SuggestedTask]


def readable(body: str | None) -> str:
    """A message's text as the model is shown it: quoted lines left out, as they are what was said before this message. A
    forward's text stays, as that is often all a forward says."""
    return "\n".join(ln for ln in (body or "").splitlines() if not _QUOTED.match(ln)).strip()[:TEXT_MAX]


def _objects(held: object) -> list[dict[str, Any]]:
    return [one for one in held if is_object(one)] if is_list(held) else []


def event_of(raw: dict[str, Any], today: date) -> SuggestedEvent | None:
    """A meeting the model named, or None for one with no title or no day we can read. An end that can't end it is left off."""
    title = proposed.one_line(raw.get("title"), proposed.TITLE_MAX)
    start = proposed.read_moment(raw.get("start"), today)
    if not title or start is None:
        return None
    end = proposed.read_moment(raw.get("end"), today)
    if end is not None and not proposed.ends_after(start, end):
        end = None
    return {"title": title, "start": start, "end": end,
            "place": proposed.one_line(raw.get("place"), proposed.PLACE_MAX), "all_day": proposed.is_day(start)}


def task_of(raw: dict[str, Any], today: date) -> SuggestedTask | None:
    """A task the model named, or None for one with no title. A date that won't read leaves it without one."""
    title = proposed.one_line(raw.get("title"), proposed.TITLE_MAX)
    return {"title": title, "due": proposed.read_date(raw.get("due"), today)} if title else None


def _distinct[T](found: Iterable[T | None], key: Callable[[T], Hashable], cap: int) -> list[T]:
    seen: set[Hashable] = set()
    out: list[T] = []
    for one in found:
        if one is None or key(one) in seen:
            continue
        seen.add(key(one))
        out.append(one)
        if len(out) == cap:
            break
    return out


def suggestions(answer: object, today: date) -> Suggestions:
    """What is taken from the model's answer: each event and task once, within the caps. A reply that won't do is none."""
    got: dict[str, Any] = answer if is_object(answer) else {}
    return {"events": _distinct((event_of(raw, today) for raw in _objects(got.get("events"))),
                                lambda e: (e["title"].lower(), e["start"]), MAX_EVENTS),
            "tasks": _distinct((task_of(raw, today) for raw in _objects(got.get("tasks"))),
                               lambda t: (t["title"].lower(), t["due"]), MAX_TASKS)}
