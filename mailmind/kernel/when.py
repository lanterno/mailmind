"""When something happened, as the app says it."""
import time
from datetime import datetime, timezone


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def rel_time(iso):
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


def ago(ts):
    """'3d'/'5h' since a unix ts."""
    if not ts:
        return "a while"
    s = max(0, int(time.time()) - int(ts))
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"
