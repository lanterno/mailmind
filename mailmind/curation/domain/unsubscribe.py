"""What a sender's mail says about leaving its list, and which attempts count as done."""
import email
import hashlib
import hmac

from mailmind.content import mime

NO_METHOD = {"method": "none", "https_uri": None, "mailto_uri": None, "mailto_to": None,
             "mailto_subject": None, "mailto_body": None}
TERMINAL = ("unsubscribed_oneclick", "mailto_sent", "needs_browser")   # never fired twice; anything else stays retryable


def confirm_token(secret, from_addr):
    """Stateless proof the human saw the preview: HMAC(the process's secret, addr). Rotates per server start."""
    return hmac.new(secret.encode(), ("cleanup:" + (from_addr or "").lower()).encode(),
                    hashlib.sha256).hexdigest()[:16]


def parse_list_unsub(raw_bytes):
    """From a raw RFC822 message → the resolvable unsubscribe method (`mime.list_unsub`)."""
    try:
        msg = email.message_from_bytes(raw_bytes or b"")
    except Exception:
        return {"method": "none", "raw": ""}
    return mime.list_unsub(mime.dec(msg.get("List-Unsubscribe")), mime.dec(msg.get("List-Unsubscribe-Post")))


def stored(rows, body_of, remember):
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
