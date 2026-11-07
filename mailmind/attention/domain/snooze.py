"""A snooze: hiding a message until it wakes. Presets read in the machine's local zone: the server is the person's machine."""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from mailmind.kernel.ids import AccountId, Pk, ThreadRef
from mailmind.kernel.parsed import required_int
from mailmind.kernel.row import Row

BULK_CAP = 500      # the most one request may snooze: the one bulk array the API takes, capped here and not only at plan time
MAX_AHEAD_DAYS = 366


def in_window(until: int, now: float) -> bool:
    """A time someone picked has to lie ahead, and within a year: (now, now + 366 days]."""
    return now < until <= now + MAX_AHEAD_DAYS * 86400


def wake_time(body: Mapping[str, Any], now: datetime) -> int | None:
    """A wake time (unix secs) from {preset} or {until}, or None if it's invalid. `now` is aware, in the local zone."""
    preset = body.get("preset")
    if preset:
        if preset == "later_today":
            t = (now + timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
            if t.date() > now.date():           # +3h ran past midnight → "later today" shouldn't wake you
                t = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)  # at 2am; next morning instead
        elif preset == "tomorrow":
            t = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
        elif preset == "weekend":
            d = (5 - now.weekday()) % 7 or 7        # upcoming Saturday
            t = (now + timedelta(days=d)).replace(hour=8, minute=0, second=0, microsecond=0)
        elif preset == "next_week":
            d = (7 - now.weekday()) % 7 or 7        # upcoming Monday
            t = (now + timedelta(days=d)).replace(hour=8, minute=0, second=0, microsecond=0)
        else:
            return None
        return int(t.timestamp())
    until = body.get("until")
    if until is not None:
        try:
            u = int(until)
        except (TypeError, ValueError, OverflowError):      # the last for a number too large to be one, which JSON may send
            try:
                u = int(datetime.fromisoformat(str(until)).astimezone().timestamp())
            except Exception:
                return None
        if in_window(u, now.timestamp()):
            return u
    return None


def is_due(until: int, now: int) -> bool:
    """It wakes at its time, not a moment after. adapters.sqlite asks this over every snooze at once, as `until<=?`."""
    return until <= now


def marks_unread_on_waking(seen: object) -> bool:
    """What wakes comes back unread, on the server too (decision D6), or the next sync that read its flags would take that
    back. One already unread asks nothing of the server."""
    return bool(seen)


def named_thread(body: Mapping[str, Any]) -> tuple[AccountId, ThreadRef] | None:
    """(account, thread id), if a request names a whole thread: that goes before any pks it also names."""
    if body.get("thread_id") and body.get("account"):
        return body["account"], body["thread_id"]
    return None


def named_pks(body: Mapping[str, Any]) -> list[Pk]:
    """The messages a request names by pk, up to BULK_CAP."""
    if isinstance(body.get("pks"), list):
        return [Pk(required_int(x)) for x in body["pks"]][:BULK_CAP]
    if body.get("pk") is not None:
        return [Pk(required_int(body["pk"]))]
    return []


def undo_entry(pks: Sequence[int], rows: Sequence[Row | None]) -> dict[str, Any]:
    """What undoing a snooze needs of each message, as `rows` had it before: when it was to wake, and where it is, since a
    pk isn't stable and the undo stack outlives it. A pk with no row has neither."""
    return {"kind": "snooze", "pks": list(pks),
            "where": [[r["message_key"], r["folder"]] if r else None for r in rows],
            "prev_until": [r["snooze_until"] if r else None for r in rows], "label": "Snoozed"}


@dataclass(frozen=True)
class Restoration:
    """One message an Undo gives back: found by `where`, its key and folder, or by `pk` in an entry from before those. It
    goes back to waking at `until`, or to not being snoozed."""
    pk: Pk
    where: Sequence[Any] | None
    until: int | None


def restorations(entry: Mapping[str, Any]) -> list[Restoration]:
    """What an undo entry gives back. A message that had no row when it was snoozed has nothing to go back to."""
    where: Sequence[Any] = entry.get("where") or []
    previous: Sequence[Any] = entry.get("prev_until", [])
    out: list[Restoration] = []
    for i, pk in enumerate(entry["pks"]):
        if i < len(where) and not where[i]:
            continue
        out.append(Restoration(pk, where[i] if i < len(where) else None, (previous[i] if i < len(previous) else None) or None))
    return out
