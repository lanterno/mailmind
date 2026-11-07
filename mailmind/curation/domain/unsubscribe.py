"""What a sender's mail says about leaving its list, and which attempts count as done."""
import email
from collections.abc import Callable, Iterable

from mailmind.content import mime
from mailmind.content.mime import UnsubWay
from mailmind.kernel.row import Row

NO_METHOD: UnsubWay = {"method": "none", "https_uri": None, "mailto_uri": None, "mailto_to": None,
                       "mailto_subject": None, "mailto_body": None, "raw": ""}
TOKEN_LIFE = 600           # seconds a preview's confirm token stands
TOKEN_PURPOSE = "cleanup"
TERMINAL = ("unsubscribed_oneclick", "mailto_sent", "needs_browser")   # never fired twice; anything else stays retryable


def preview_said(from_addr: str | None, info: UnsubWay) -> tuple[str, str, str | None]:
    """What a preview's confirm token stands for: what would go out, and where. A list that changes its way out since
    then needs a fresh look."""
    target = info.get("mailto_to") if info["method"] == "mailto" else info.get("https_uri")
    return (from_addr or "").lower(), info["method"], target


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
