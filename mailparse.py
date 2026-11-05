"""Parse a raw RFC822 message into renderable parts — stdlib email only.

parse(raw_bytes) -> {
  "text": plain-text body or None,
  "html": HTML body or None (UNSANITIZED — caller must run sanitize_html),
  "attachments": [{part_id, filename, content_type, size, content_id, is_inline}],
  "to","cc","reply_to","date": header strings,
}
The body is chosen from multipart/alternative the way a client does (prefer html).
cid: inline images are surfaced so the sanitizer/UI can resolve them later.
"""
import email
from email import policy
from email.header import decode_header


def walk(msg):
    """Like Message.walk, but an attached email is one part: what it holds isn't this message's."""
    yield msg
    if msg.is_multipart() and msg.get_content_maintype() != "message":
        for sub in msg.get_payload():
            yield from walk(sub)


def part_bytes(part):
    """A part's bytes. An attached email is parsed, with no encoded body of its own to decode."""
    if part.get_content_maintype() == "message" and part.is_multipart():
        try:
            return part.get_payload(0).as_bytes()
        except Exception:
            return b""
    return part.get_payload(decode=True) or b""


def _hdr(msg, name):
    v = msg.get(name)
    if not v:
        return ""
    out = []
    for part, enc in decode_header(v):
        if isinstance(part, bytes):
            try:
                out.append(part.decode(enc or "utf-8", "replace"))
            except (LookupError, Exception):
                out.append(part.decode("utf-8", "replace"))
        else:
            out.append(part)
    return "".join(out).strip()


def _decode(part):
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        return payload.decode(part.get_content_charset() or "utf-8", "replace")
    except Exception:
        return ""


def parse(raw_bytes):
    try:
        msg = email.message_from_bytes(raw_bytes, policy=policy.default)
    except Exception:
        msg = email.message_from_bytes(raw_bytes or b"")
    text, html, cal = None, None, None
    attachments = []
    idx = 0
    for part in walk(msg):
        if part.get_content_maintype() == "multipart":
            continue
        idx += 1
        ctype = (part.get_content_type() or "").lower()
        disp = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        cid = (part.get("Content-ID") or "").strip().strip("<>")
        is_attachment = disp == "attachment" or (filename and disp != "inline") or ctype == "message/rfc822"
        if is_attachment or (filename and not cid):
            payload = part_bytes(part)
            attachments.append({
                "part_id": str(idx),
                "filename": _decode_fn(filename) or ("message.eml" if ctype == "message/rfc822" else f"part-{idx}"),
                "content_type": ctype, "size": len(payload),
                "content_id": cid or None, "is_inline": int(disp == "inline" or bool(cid)),
            })
            continue
        if cid:  # an inline image referenced by cid: — record it, don't inline the body
            payload = part.get_payload(decode=True) or b""
            attachments.append({
                "part_id": str(idx), "filename": _decode_fn(filename) or f"inline-{idx}",
                "content_type": ctype, "size": len(payload),
                "content_id": cid, "is_inline": 1,
            })
            continue
        if ctype == "text/plain" and text is None:
            text = _decode(part)
        elif ctype == "text/html" and html is None:
            html = _decode(part)
        elif ctype == "text/calendar" and cal is None:
            cal = (idx, part)
    # Outlook sends an invite only as an unnamed inline text/calendar part; record it like the
    # invite.ics file other clients attach (and only when no such file carries the invite already)
    if cal and not any("calendar" in a["content_type"] or a["content_type"] == "application/ics"
                       or a["filename"].lower().endswith(".ics") for a in attachments):
        attachments.append({
            "part_id": str(cal[0]), "filename": "invite.ics", "content_type": "text/calendar",
            "size": len(cal[1].get_payload(decode=True) or b""), "content_id": None, "is_inline": 1,
        })
    return {
        "text": text, "html": html, "attachments": attachments,
        "to": _hdr(msg, "To"), "cc": _hdr(msg, "Cc"),
        "reply_to": _hdr(msg, "Reply-To"), "date": _hdr(msg, "Date"),
    }


def _decode_fn(name):
    if not name:
        return None
    try:
        return "".join(
            (p.decode(e or "utf-8", "replace") if isinstance(p, bytes) else p)
            for p, e in decode_header(name))
    except Exception:
        return name


def extract_part(raw_bytes, part_id):
    """Re-walk a raw RFC822 message EXACTLY like parse() and return
    (payload_bytes, content_type) for the non-multipart part whose running 1-based
    index == int(part_id), else (None, None). Stays in lockstep with parse()'s idx
    scheme so any part_id parse() emitted resolves to the same bytes."""
    try:
        want = int(part_id)
    except (TypeError, ValueError):
        return None, None
    try:
        msg = email.message_from_bytes(raw_bytes, policy=policy.default)
    except Exception:
        msg = email.message_from_bytes(raw_bytes or b"")
    idx = 0
    for part in walk(msg):
        if part.get_content_maintype() == "multipart":
            continue                       # IDENTICAL skip to parse(): multiparts never increment idx
        idx += 1
        if idx == want:
            data = part_bytes(part)
            ctype = (part.get_content_type() or "application/octet-stream").lower()
            return data, ctype
    return None, None


def inline_parts(raw_bytes):
    """{content_id: (content_type, bytes)} for cid-referenced inline images, so the
    server can rewrite cid: -> data: in the sanitized body. Raster images only is
    enforced by the caller; here we just surface every cid part's bytes."""
    out = {}
    try:
        msg = email.message_from_bytes(raw_bytes, policy=policy.default)
    except Exception:
        return out
    for part in walk(msg):
        if part.get_content_maintype() == "multipart":
            continue
        cid = (part.get("Content-ID") or "").strip().strip("<>")
        if cid:
            out[cid] = ((part.get_content_type() or "").lower(),
                        part.get_payload(decode=True) or b"")
    return out


