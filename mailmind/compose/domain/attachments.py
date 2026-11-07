"""A message's files: those a composer sends, decoded and capped, and those a forward takes along."""
import base64
import email
import email.policy
from typing import NotRequired, TypedDict

from mailmind.compose.domain import limits
from mailmind.content import mime
from mailmind.kernel.parsed import is_list, is_object


class Attachment(TypedDict):
    """A file a message carries. `params` are its own Content-Type parameters, as an iMIP reply's method."""
    filename: str
    content_type: str
    data: bytes
    params: NotRequired[dict[str, str]]


def decode(items: object) -> list[Attachment]:
    """Decode client base64 attachments -> [{filename,content_type,data}], capped."""
    if items is None:
        items = []
    if not is_list(items):
        raise ValueError("bad attachments")
    out: list[Attachment] = []
    total = 0
    for a in items:
        if not is_object(a):
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


def take_along(have: list[Attachment], found: list[Attachment]) -> list[Attachment]:
    """Of the files a forward's original holds, those that go with the ones you added under the budget: the first that
    doesn't fit ends it."""
    total = sum(len(a["data"]) for a in have)
    taken: list[Attachment] = []
    for a in found:
        if total + len(a["data"]) > limits.MAX_ATTACH_BYTES:
            break
        total += len(a["data"])
        taken.append(a)
    return taken


def extract(raw: bytes | None) -> list[Attachment]:
    """Pull real attachment parts (filename+bytes) out of a stored RFC822 message,
    for re-attaching on forward. Inline cid images are skipped."""
    try:
        msg = email.message_from_bytes(raw or b"", policy=email.policy.default)
    except Exception:
        return []
    out: list[Attachment] = []
    total = 0
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
