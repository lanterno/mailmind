"""What a message's headers come to as it is stored."""
from datetime import timezone
from email.utils import parsedate_to_datetime

RECIPIENTS_CHARS = 20000        # of the To and Cc text kept for search: a long list, not a forged one's whole length


def recipients_text(to: str | None, cc: str | None) -> str:
    """To and Cc as the one searchable string the to: and cc: operators read, names and addresses, bounded."""
    return ((to or "") + " " + (cc or "")).strip()[:RECIPIENTS_CHARS]


def dated(header: str) -> tuple[str | None, int]:
    """A Date header as (ISO time, unix time), a zone left out read as UTC, and (None, 0) for one that won't read."""
    try:
        when = parsedate_to_datetime(header)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when.isoformat(), int(when.timestamp())
    except Exception:
        return None, 0


def references(header: str | None) -> str:
    """A References header on one line: a folded one reads the same."""
    return (header or "").replace("\n", " ").strip()
