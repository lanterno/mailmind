"""A message's files: those a composer sends, decoded and capped, and those a forward takes along."""
import base64
import email
import email.policy
from typing import Literal, NotRequired, TypedDict, get_args

from mailmind.compose.domain import limits, markdown
from mailmind.content import mime
from mailmind.kernel.parsed import is_list, is_object


type PictureType = Literal["image/png", "image/jpeg", "image/gif", "image/webp"]      # what a composer may put in the text
_PICTURES = frozenset(get_args(PictureType.__value__))


class Attachment(TypedDict):
    """A file a message carries. `params` are its own Content-Type parameters, as an iMIP reply's method. `cid` is the token a
    mark in the text names a picture by."""
    filename: str
    content_type: str
    data: bytes
    params: NotRequired[dict[str, str]]
    cid: NotRequired[str]


def decode(items: object) -> list[Attachment]:
    """Decode client base64 attachments -> [{filename,content_type,data}], capped."""
    if items is None:
        items = []
    if not is_list(items):
        raise ValueError("bad attachments")
    out: list[Attachment] = []
    total = 0
    for a in items:
        if not is_object(a) or not isinstance(a.get("filename"), str | None):
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
        item: Attachment = {"filename": a.get("filename") or "attachment", "content_type": ct, "data": data}
        cid = a.get("cid")
        if cid is not None and not (isinstance(cid, str) and markdown.TOKEN.fullmatch(cid)):
            raise ValueError("bad attachment")
        if cid is not None and ct.split(";")[0].strip().lower() in _PICTURES:
            item["cid"] = cid
        out.append(item)
    return out


def pictures_of(files: list[Attachment]) -> frozenset[str]:
    """The tokens of those a mark in the text may show."""
    return frozenset(a["cid"] for a in files if "cid" in a)


def in_the_text(html: str | None, files: list[Attachment]) -> tuple[list[Attachment], list[Attachment]]:
    """(those the HTML shows, the rest). A picture whose mark is gone from the text still goes, as a file like any other."""
    def shown(a: Attachment) -> bool:
        return html is not None and "cid" in a and f"cid:{markdown.content_id(a['cid'])}" in html
    return [a for a in files if shown(a)], [a for a in files if not shown(a)]


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
    """Pull real attachment parts (filename+bytes) out of a stored RFC822 message, for re-attaching on forward: those
    `mime.travels` names, up to the budget."""
    try:
        msg = email.message_from_bytes(raw or b"", policy=email.policy.default)
    except Exception:
        return []
    out: list[Attachment] = []
    total = 0
    for part in mime.walk(msg):
        if part.get_content_maintype() == "multipart" or not mime.travels(part):
            continue
        data = mime.part_bytes(part)
        total += len(data)
        if total > limits.MAX_ATTACH_BYTES:
            break
        ctype = part.get_content_type()
        out.append({"filename": part.get_filename() or ("message.eml" if ctype == "message/rfc822" else "attachment"),
                    "content_type": ctype, "data": data})
    return out
