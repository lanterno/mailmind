"""Which messages are past their shelf life. Strictly past it, and an unknown age never expires."""
import email
from collections.abc import Iterable, Mapping
from datetime import datetime
from email.utils import parsedate_to_datetime

from mailmind.content import mime
from mailmind.content.mime import AnyMessage, dec
from mailmind.curation.domain.live import Aged, Audited, Expired
from mailmind.curation.domain.rulebook import expiry_hours

AGE_FIELDS = ("FROM", "SUBJECT", "MESSAGE-ID", "DATE")     # what expiry asks the server for


def arrival_ts(arrived: datetime | None, msg: AnyMessage) -> float | None:
    """Epoch seconds a message arrived: IMAP INTERNALDATE (`arrived`, a datetime), else its Date
    header, else None (an unknown age never expires)."""
    if arrived is not None:
        return arrived.timestamp()
    try:
        return parsedate_to_datetime(msg.get("Date", "")).timestamp()
    except (TypeError, ValueError, IndexError):
        return None


def aged_from_headers(uid: int, raw: bytes, arrived: datetime | None) -> Aged:
    """A message as expiry reads it, from its AGE_FIELDS and when the server says it arrived."""
    msg = email.message_from_bytes(raw)
    return {"uid": str(uid), "from": mime.sender_of(msg)[1].lower(),
            "subject": dec(msg.get("Subject", "")),
            "mid": (msg.get("Message-ID", "") or "").strip(),
            "ts": arrival_ts(arrived, msg)}


def select_expired(rows: Iterable[Aged], expire: Mapping[str, int], now: float) -> list[Expired]:
    """Rows whose sender has an expiry rule and whose arrival is older than it, each with its `hours`. Pure — no IMAP."""
    out: list[Expired] = []
    for r in rows:
        hours = expiry_hours(r["from"], expire)
        ts = r.get("ts")
        if hours and ts is not None and now - ts > hours * 3600:
            out.append({**r, "hours": hours})
    return out


def audited(targets: Iterable[Expired]) -> list[Audited]:
    """The audit's line for each message expiry takes, written before any of them moves."""
    return [{"uid": t["uid"], "from": t["from"], "subject": t["subject"], "bucket": "expired",
             "reason": f"expire rule: older than {t['hours']}h"} for t in targets]
