"""Parse a raw RFC822 message into renderable parts.

parse(raw_bytes) -> {
  "text": plain-text body or None,
  "html": HTML body or None (UNSANITIZED — caller must run content.sanitize),
  "attachments": [{part_id, filename, content_type, size, content_id, is_inline}],
  "to","cc","date": header strings,
  "reply_to": the address a reply goes to, bare, or "",
}
The body is chosen from multipart/alternative the way a client does (prefer html).
cid: inline images are surfaced so the sanitizer/UI can resolve them later.

`parse`, `extract_part` and `inline_parts` read raw bytes in the core (mm-content, src/mime.rs), on mail-parser, with
the interpreter released. What reads a Python `email` message a caller already holds stays here.
"""
import re
import urllib.parse
from collections.abc import Iterator
from email.header import Header, decode_header
from email.message import Message
from email.utils import getaddresses, parseaddr
from typing import Any, TypedDict, TypeIs

import mailmind_leaves

_FOLD = re.compile(r"\r?\n[ \t]+")

# A message under any policy. A header off a compat32 one is a str or a Header, off policy.default one the
# registry's, and only the policy in force knows which, so what reads one says Any on purpose.
type AnyMessage = Message[Any, Any]


class Attachment(TypedDict):
    part_id: str
    filename: str
    content_type: str
    size: int
    content_id: str | None
    is_inline: int
    travels: bool                # a forward takes it along, as `travels` says


class Parsed(TypedDict):
    """A raw message as `parse` reads it. `html` is unsanitized."""
    text: str | None
    html: str | None
    attachments: list[Attachment]
    to: str
    cc: str
    reply_to: str                # the bare address, read before any encoded word is decoded
    date: str


def _is_message(payload: object) -> TypeIs[AnyMessage]:
    """What a multipart holds is a message, from a parser. The stubs also allow a str."""
    return isinstance(payload, Message)


def _unfold(value: str | Header) -> str | Header:
    """A header value as one line. decode_header drops the space a fold leaves next to an encoded
    word, and parseaddr can't read an address across one. A raw 8-bit value is a Header: not a str."""
    return _FOLD.sub(" ", value) if isinstance(value, str) else value


_UNSUB_POST, _UNSUB_RAW_MAX = " | POST:", 4000


class UnsubWay(TypedDict):
    """The way out of a list that a message's headers offer. `method` is `one-click`, `mailto`, `http-page` or `none`."""
    method: str
    https_uri: str | None
    mailto_uri: str | None
    mailto_to: str | None
    mailto_subject: str | None
    mailto_body: str | None
    raw: str


def list_unsub(lu: str | None, post: str | None = "") -> UnsubWay:
    """The way to unsubscribe that a message's headers offer, from List-Unsubscribe (`lu`, unfolded) and
    List-Unsubscribe-Post. RFC 2369 holds <mailto:…> and/or <https://…>; RFC 8058 one-click needs BOTH an
    https URI AND `List-Unsubscribe-Post: List-Unsubscribe=One-Click`. A bare https URI is a landing PAGE
    (open in a browser, NEVER POST). Defaults to the SAFE side ('none') when ambiguous. `raw` is what
    `list_unsub_stored` reads it all back from."""
    lu = (lu or "").strip()
    lup = re.sub(r"\s+", "", post or "").lower()
    uris = [re.sub(r"\s+", "", u) for u in re.findall(r"<([^>]+)>", lu)]    # RFC 2369: whitespace within one is ignored
    https_uri = next((u for u in uris if u.lower().startswith("https://")), None)
    mailto_uri = next((u for u in uris if u.lower().startswith("mailto:")), None)
    one_click = bool(https_uri) and lup == "list-unsubscribe=one-click"
    if one_click:
        method = "one-click"
    elif mailto_uri:
        method = "mailto"
    elif https_uri:
        method = "http-page"
    else:
        method = "none"
    mailto_to = mailto_subject = mailto_body = None
    if mailto_uri:
        m = urllib.parse.urlparse(mailto_uri)
        mailto_to = urllib.parse.unquote(m.path or "").strip() or None
        q = urllib.parse.parse_qs(m.query)
        mailto_subject = (q.get("subject") or [None])[0]
        mailto_body = (q.get("body") or [None])[0]
    raw_cache = (lu + (_UNSUB_POST + lup if lup else ""))[:_UNSUB_RAW_MAX]
    return {"method": method, "https_uri": https_uri, "mailto_uri": mailto_uri,
            "mailto_to": mailto_to, "mailto_subject": mailto_subject, "mailto_body": mailto_body,
            "raw": raw_cache}


def list_unsub_stored(raw: str | None) -> UnsubWay:
    """What `list_unsub` said, from the `raw` it gave."""
    lu, _, post = (raw or "").partition(_UNSUB_POST)
    return list_unsub(lu, post)


def dec(value: str | Header | None) -> str:
    """A header's text, with its encoded words decoded."""
    if not value:
        return ""
    out: list[str] = []
    for part, enc in decode_header(_unfold(value)):
        if isinstance(part, bytes):
            try:
                out.append(part.decode(enc or "utf-8", "replace"))
            except (LookupError, Exception):  # unknown/invalid charset -> safe fallback
                out.append(part.decode("utf-8", "replace"))
        else:
            out.append(part)
    return "".join(out).replace("\r", " ").replace("\n", " ").strip()


def recipient_addrs(msg: AnyMessage) -> list[str]:
    """Who a compat32 message went to, To and Cc, as bare lowercase addresses. Read before any encoded word
    is decoded, so a display name can't pass for an address, and a malformed entry costs only itself."""
    values = [str(_unfold(v)) for field in ("To", "Cc") for v in msg.get_all(field) or []]
    return sorted({a.strip(".'").lower() for _, a in getaddresses(values, strict=False) if "@" in a})


def sender_of(msg: AnyMessage) -> tuple[str, str]:
    """(name, address) from a compat32 message's From. The name still holds its encoded words."""
    sender = msg.get("From", "")
    return parseaddr(_FOLD.sub(" ", sender) if isinstance(sender, str) else dec(sender))


def walk(msg: AnyMessage) -> Iterator[AnyMessage]:
    """Like Message.walk, but an attached email is one part: what it holds isn't this message's."""
    yield msg
    if msg.is_multipart() and msg.get_content_maintype() != "message":
        for sub in msg.get_payload():
            if _is_message(sub):
                yield from walk(sub)


def decoded(part: AnyMessage) -> bytes | None:
    """A part's body with its transfer encoding undone, or None for a multipart. The stubs also allow a
    Message, which decode=True never gives."""
    payload = part.get_payload(decode=True)
    return payload if isinstance(payload, bytes) else None


def part_bytes(part: AnyMessage) -> bytes:
    """A part's bytes. An attached email is parsed, with no encoded body of its own to decode."""
    if part.get_content_maintype() == "message" and part.is_multipart():
        try:
            attached = part.get_payload(0)
            return attached.as_bytes() if _is_message(attached) else b""
        except Exception:
            return b""
    return decoded(part) or b""


def holds_no_parts(part: AnyMessage) -> bool:
    """A multipart that named no boundary: what it holds is plain text, and no reader lists it."""
    return part.get_content_maintype() == "multipart" and not part.is_multipart()


def travels(part: AnyMessage) -> bool:
    """Whether a forward takes this part along: a file someone attached, or an attached email. A picture the text shows by
    its Content-ID stays with the text, and so does a named part marked inline."""
    disp = (part.get_content_disposition() or "").lower()
    return (disp == "attachment" or part.get_content_type() == "message/rfc822"
            or bool(part.get_filename() and disp != "inline" and not _hdr(part, "Content-ID").strip("<>")))


def _hdr(msg: AnyMessage, name: str) -> str:
    try:
        v = msg.get(name)
    except ValueError:     # policy.default refuses a value whose encoded words decode to a line break
        v = next((raw for key, raw in msg.raw_items() if key.lower() == name.lower()), None)
    return dec(v)


_SIGNATURE = "-- "


def _marked(depth: int, text: str) -> str:
    return ">" * depth + (" " + text if text else "") if depth else text


def _unflowed(text: str, delsp: bool) -> str:
    """A format=flowed text (RFC 3676) as it was written. Of each line: its quote depth, then one stuffing space off, and one
    still ending in a space flows on to the next at its depth. `-- ` above a signature ends in one and is neither, and another
    depth or an empty line ends a paragraph all the same."""
    joint = "" if delsp else " "
    out: list[str] = []
    paragraph: list[str] = []
    depth = 0
    for line in text.replace("\r\n", "\n").split("\n"):
        at = len(line) - len(line.lstrip(">"))
        piece = line[at:].removeprefix(" ")
        if paragraph and (at != depth or not piece or piece == _SIGNATURE):
            out.append(_marked(depth, joint.join(paragraph)))
            paragraph = []
        depth = at
        if piece.endswith(" ") and piece != _SIGNATURE:
            paragraph.append(piece[:-1])
        else:
            out.append(_marked(at, joint.join([*paragraph, piece])))
            paragraph = []
    if paragraph:
        out.append(_marked(depth, joint.join(paragraph)))
    return "\n".join(out)


def as_written(part: AnyMessage, text: str) -> str:
    """A text/plain part's decoded text as its writer typed it: format=flowed's soft breaks undone, any other as it came."""
    try:
        if part.get_content_type() != "text/plain" or str(part.get_param("format") or "").lower() != "flowed":
            return text
        delsp = str(part.get_param("delsp") or "").lower() == "yes"
    except Exception:
        return text
    return _unflowed(text, delsp)


def _raw(raw_bytes: bytes | None) -> bytes:
    """The bytes the core reads: a bytes object as it is, without a copy."""
    return raw_bytes if type(raw_bytes) is bytes else bytes(raw_bytes or b"")


def parse(raw_bytes: bytes) -> Parsed:
    text, html, found, to, cc, reply_to, date = mailmind_leaves.mime_parse(_raw(raw_bytes))
    attachments: list[Attachment] = [
        {"part_id": str(part_id), "filename": filename, "content_type": content_type, "size": size,
         "content_id": content_id, "is_inline": int(is_inline), "travels": goes}
        for part_id, filename, content_type, size, content_id, is_inline, goes in found]
    return {"text": text, "html": html, "attachments": attachments, "to": to, "cc": cc, "reply_to": reply_to, "date": date}


def extract_part(raw_bytes: bytes, part_id: str | int) -> tuple[bytes, str] | tuple[None, None]:
    """(payload_bytes, content_type) for the part `parse` numbered `part_id`, else (None, None): the same walk, so
    any part_id parse() emitted resolves to the same bytes. An attached email comes as it was sent."""
    try:
        want = int(part_id)
    except (TypeError, ValueError):
        return None, None
    if not 0 < want < 2**32:
        return None, None
    found = mailmind_leaves.mime_extract_part(_raw(raw_bytes), want)
    return (None, None) if found is None else found


def inline_parts(raw_bytes: bytes) -> dict[str, tuple[str, bytes]]:
    """{content_id: (content_type, bytes)} for cid-referenced inline images, so the
    server can rewrite cid: -> data: in the sanitized body. Raster images only is
    enforced by the caller; here we just surface every cid part's bytes."""
    return {cid: (content_type, data) for cid, content_type, data in mailmind_leaves.mime_inline_parts(_raw(raw_bytes))}


def has_invite(msg: AnyMessage) -> bool:
    """Whether a message carries a calendar invite: a text/calendar or application/ics part, or an .ics file."""
    try:
        return any(p.get_content_type() in ("text/calendar", "application/ics")
                   or (p.get_filename() or "").lower().endswith(".ics") for p in walk(msg))
    except Exception:
        return False
