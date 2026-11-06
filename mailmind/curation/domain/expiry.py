"""Which messages are past their shelf life. Strictly past it, and an unknown age never expires."""
from collections.abc import Iterable, Mapping
from datetime import datetime
from email.utils import parsedate_to_datetime

from mailmind.content.mime import AnyMessage
from mailmind.curation.domain.live import Aged, Expired
from mailmind.curation.domain.rulebook import expiry_hours


def arrival_ts(arrived: datetime | None, msg: AnyMessage) -> float | None:
    """Epoch seconds a message arrived: IMAP INTERNALDATE (`arrived`, a datetime), else its Date
    header, else None (an unknown age never expires)."""
    if arrived is not None:
        return arrived.timestamp()
    try:
        return parsedate_to_datetime(msg.get("Date", "")).timestamp()
    except (TypeError, ValueError, IndexError):
        return None


def select_expired(rows: Iterable[Aged], expire: Mapping[str, int], now: float) -> list[Expired]:
    """Rows whose sender has an expiry rule and whose arrival is older than it, each with its `hours`. Pure — no IMAP."""
    out: list[Expired] = []
    for r in rows:
        hours = expiry_hours(r["from"], expire)
        ts = r.get("ts")
        if hours and ts is not None and now - ts > hours * 3600:
            out.append({**r, "hours": hours})
    return out
