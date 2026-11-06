"""A message's files: those a composer sends, decoded and capped, and those a forward takes along."""
import base64
import email
import email.policy

from mailmind.compose.domain import limits
from mailmind.content import mime


def decode(items):
    """Decode client base64 attachments -> [{filename,content_type,data}], capped."""
    if items is None:
        items = []
    if not isinstance(items, list):
        raise ValueError("bad attachments")
    out, total = [], 0
    for a in items:
        if not isinstance(a, dict):
            raise ValueError("bad attachment")
        try:
            data = base64.b64decode(a.get("b64") or "", validate=True)
        except Exception:
            raise ValueError("bad attachment encoding")
        total += len(data)
        if total > limits.MAX_ATTACH_BYTES:
            raise ValueError("attachments too large")
        ct = str(a.get("content_type") or "application/octet-stream")
        if ct.lower().startswith("multipart/"):          # a browser types .mhtml multipart/related: written
            ct = "application/octet-stream"              # as one, it has no boundary and every reader skips it
        out.append({"filename": a.get("filename") or "attachment", "content_type": ct, "data": data})
    return out


def extract(raw):
    """Pull real attachment parts (filename+bytes) out of a stored RFC822 message,
    for re-attaching on forward. Inline cid images are skipped."""
    try:
        msg = email.message_from_bytes(raw or b"", policy=email.policy.default)
    except Exception:
        return []
    out, total = [], 0
    for part in mime.walk(msg):
        if part.get_content_maintype() == "multipart":
            continue
        disp = (part.get_content_disposition() or "").lower()
        fn = part.get_filename()
        cid = (part.get("Content-ID") or "").strip()
        attached_email = part.get_content_type() == "message/rfc822"
        if disp == "attachment" or (fn and disp != "inline" and not cid) or attached_email:
            data = mime.part_bytes(part)
            total += len(data)
            if total > limits.MAX_ATTACH_BYTES:
                break
            out.append({"filename": fn or ("message.eml" if attached_email else "attachment"),
                        "content_type": part.get_content_type(), "data": data})
    return out
