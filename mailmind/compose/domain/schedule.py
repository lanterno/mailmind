"""The time a person asks a send to go at. It is judged before the original is fetched, so a bad one costs nothing."""
from typing import Any

from mailmind.kernel.errors import Refused

FURTHEST = 366 * 86400     # seconds ahead a send may be scheduled


def send_after(asked: Any, now: float) -> int:
    """Epoch seconds, or 0 for now, after the grace window. A time that has passed is refused rather than quietly sent
    now: whoever scheduled it isn't there to say so."""
    try:
        at = int(asked or 0)
    except (TypeError, ValueError, OverflowError):     # JSON may say Infinity, which no int holds
        raise Refused("bad send time")
    if at and at <= now:
        raise Refused("that send time has passed")
    if at and at >= now + FURTHEST:
        raise Refused("pick a send time within a year")
    return at
