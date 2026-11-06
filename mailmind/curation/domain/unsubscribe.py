"""What a sender's mail says about leaving its list, and which attempts count as done."""
import email
import hashlib
import hmac
import json
from collections.abc import Callable, Iterable

from mailmind.content import mime
from mailmind.content.mime import UnsubWay
from mailmind.kernel.row import Row

NO_METHOD: UnsubWay = {"method": "none", "https_uri": None, "mailto_uri": None, "mailto_to": None,
                       "mailto_subject": None, "mailto_body": None, "raw": ""}
TOKEN_LIFE = 600           # seconds a preview's confirm_token stands
TERMINAL = ("unsubscribed_oneclick", "mailto_sent", "needs_browser")   # never fired twice; anything else stays retryable


def _plan_mac(secret: str, from_addr: str | None, info: UnsubWay, ts: int) -> str:
    target = info.get("mailto_to") if info["method"] == "mailto" else info.get("https_uri")
    plan = json.dumps(["cleanup", (from_addr or "").lower(), info["method"], target, ts])
    return hmac.new(secret.encode(), plan.encode(), hashlib.sha256).hexdigest()[:16]


def confirm_token(secret: str, from_addr: str | None, info: UnsubWay, now: float) -> str:
    """Stateless proof the human saw the preview: what it said would go out, where, and when they saw it. `info` is
    what resolve gave. Rotates per server start."""
    ts = int(now)
    return f"{ts}.{_plan_mac(secret, from_addr, info, ts)}"


def token_in_date(token: str, now: float) -> bool:
    """Is this a token at all, and of the last few minutes? Asked first, since going on may read from a server."""
    ts = token.partition(".")[0]
    return ts.isascii() and ts.isdigit() and len(ts) <= 12 and 0 <= int(now) - int(ts) <= TOKEN_LIFE


def token_names(secret: str, token: str, from_addr: str | None, info: UnsubWay) -> bool:
    """Did the preview say what would go out now? A list that changed its way out since then needs a fresh look."""
    ts, _, mac = token.partition(".")
    return hmac.compare_digest(mac.encode(), _plan_mac(secret, from_addr, info, int(ts)).encode())


def parse_list_unsub(raw_bytes: bytes | None) -> UnsubWay:
    """From a raw RFC822 message → the resolvable unsubscribe method (`mime.list_unsub`)."""
    try:
        msg = email.message_from_bytes(raw_bytes or b"")
    except Exception:
        return NO_METHOD
    return mime.list_unsub(mime.dec(msg.get("List-Unsubscribe")), mime.dec(msg.get("List-Unsubscribe-Post")))


def stored(rows: Iterable[Row], body_of: Callable[[int], bytes | None],
           remember: Callable[[int, UnsubWay], None]) -> UnsubWay | None:
    """What the sender's newest message that can say says, newest first through `rows`: from what ingest
    kept of its headers, else from its stored body. None if no row can say. `body_of(pk)` gives a stored
    body or None, and `remember(pk, parsed)` keeps what a body said."""
    for r in rows:
        m = r["list_unsub_method"]
        if m == "none":
            return NO_METHOD                            # cached authoritative none → stop (no re-fetch)
        if m:
            parsed = mime.list_unsub_stored(r["list_unsub_raw"])
            if parsed["method"] != "none":
                return parsed
        raw = body_of(r["pk"])
        if raw is None:
            continue                                    # body not fetched yet → can't resolve this row
        parsed = parse_list_unsub(raw)
        if not m:                                       # uncached → cache the resolution
            remember(r["pk"], parsed)
        return parsed if parsed["method"] != "none" else NO_METHOD    # newest with a body has no header → none
    return None
