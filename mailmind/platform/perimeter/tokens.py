"""A confirm token: `<time>.<mac>`, proof that this run of the server showed a person this very thing a moment ago.

The mac is an HMAC over a purpose, what was shown and the time, under the process's secret, so a restart ends every token,
one purpose's can't stand for another, and its time can't be moved on.
"""
import hashlib
import hmac
import json
from collections.abc import Sequence


def _mac(secret: str, purpose: str, subject: Sequence[str | None], ts: int) -> str:
    said = json.dumps([purpose, *subject, ts])
    return hmac.new(secret.encode(), said.encode(), hashlib.sha256).hexdigest()[:16]


def mint(secret: str, purpose: str, subject: Sequence[str | None], now: float) -> str:
    ts = int(now)
    return f"{ts}.{_mac(secret, purpose, subject, ts)}"


def in_date(token: str, now: float, life: int) -> bool:
    """Is this a token at all, and of the last `life` seconds? It can be asked first, when going on costs something."""
    ts = token.partition(".")[0]
    return ts.isascii() and ts.isdigit() and len(ts) <= 12 and 0 <= int(now) - int(ts) <= life


def names(secret: str, token: str, purpose: str, subject: Sequence[str | None]) -> bool:
    """Was it minted for this very thing? Ask `in_date` first: only a token in date is read."""
    ts, _, mac = token.partition(".")
    return hmac.compare_digest(mac.encode(), _mac(secret, purpose, subject, int(ts)).encode())
