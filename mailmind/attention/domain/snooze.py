"""When a snooze wakes. Presets read in the machine's local zone: the server is the person's machine."""
from datetime import datetime, timedelta

BULK_CAP = 500      # the most one request may snooze: the one bulk array the API takes, capped here and not only at plan time


def wake_time(body, now):
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
        except (TypeError, ValueError):
            try:
                u = int(datetime.fromisoformat(str(until)).astimezone().timestamp())
            except Exception:
                return None
        if now.timestamp() < u <= now.timestamp() + 366 * 86400:   # future, within a year
            return u
    return None
