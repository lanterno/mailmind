"""Which messages are past their shelf life. Strictly past it, and an unknown age never expires."""
from email.utils import parsedate_to_datetime

from mailmind.curation.domain.rulebook import expiry_hours


def arrival_ts(arrived, msg):
    """Epoch seconds a message arrived: IMAP INTERNALDATE (`arrived`, a datetime), else its Date
    header, else None (an unknown age never expires)."""
    if arrived is not None:
        return arrived.timestamp()
    try:
        return parsedate_to_datetime(msg.get("Date", "")).timestamp()
    except (TypeError, ValueError, IndexError):
        return None


def select_expired(rows, expire, now):
    """Rows whose sender has an expiry rule and whose arrival is older than it. Pure — no IMAP.
    Tags each hit with r['hours']."""
    out = []
    for r in rows:
        hours = expiry_hours(r["from"], expire)
        if hours and r.get("ts") is not None and now - r["ts"] > hours * 3600:
            r["hours"] = hours
            out.append(r)
    return out
