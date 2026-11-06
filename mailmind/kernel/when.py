"""When something happened, as the app says it."""
import time
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel_time(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        if delta.days == 0:
            h = delta.seconds // 3600
            return "just now" if h == 0 else f"{h}h ago"
        if delta.days == 1:
            return "yesterday"
        if delta.days < 7:
            return f"{delta.days}d ago"
        if delta.days < 30:
            return f"{delta.days // 7}w ago"
        return dt.strftime("%b %d")
    except Exception:
        return iso[:10]


def ago(ts: float | str | None) -> str:
    """'3d'/'5h' since a unix ts."""
    if not ts:
        return "a while"
    s = max(0, int(time.time()) - int(ts))
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def rel_time_future(ts: float | str) -> str:
    """Human 'wakes ...' label for a future unix timestamp."""
    try:
        dt = datetime.fromtimestamp(int(ts)).astimezone()
        now = datetime.now().astimezone()
        secs = (dt - now).total_seconds()
        if secs < 0:
            return "soon"
        if secs < 3600:
            return f"in {int(secs // 60)}m"
        if dt.date() == now.date():
            return dt.strftime("today %-I:%M %p")
        if (dt.date() - now.date()).days == 1:
            return dt.strftime("tomorrow %-I:%M %p")
        if (dt.date() - now.date()).days < 7:
            return dt.strftime("%a %-I:%M %p")
        return dt.strftime("%b %-d")
    except Exception:
        return ""
