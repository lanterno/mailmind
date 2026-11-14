"""Reading a raw message, now the Rust core's (mm-content, src/mime.rs, on mail-parser), holds to what the Python app's
`email` package read before it: the same text, HTML, parts, part ids and headers. Sync stores what `parse` says, and a
part id it stored names a part to `extract_part` for as long as the message is kept, so an answer that drifted would show
mail otherwise than it was stored, or hand over the wrong file.

The Python reading is kept below as it was, as the oracle. Where the two differ on purpose, a test at the end says what
the core answers and why that is the better answer, and the hand-made cases name the difference they show. Only mail
read from now on reads the new way: what a sync stored stays as it was stored.
core/crates/mm-content/tests/mime.rs pins answers on the Rust side.
"""
import base64
import email
import os
import quopri
import re
from datetime import UTC, datetime, timedelta, timezone
from email import policy
from email.header import decode_header
from email.utils import format_datetime, getaddresses

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mailmind.content import mime
from mailmind.content.mime import AnyMessage, Attachment, Parsed, as_written, dec, decoded, holds_no_parts, part_bytes, travels, walk

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
CORPUS = os.path.join(ROOT, "tests", "fixtures", "corpus")

# ── the oracle: parse, extract_part and inline_parts of mailmind/content/mime/message.py as they were ─────────────────
# What they called and still lives in that module (dec, walk, decoded, part_bytes, holds_no_parts, travels, as_written)
# reads a Python message, and is imported.


def python_hdr(msg: AnyMessage, name: str) -> str:
    try:
        v = msg.get(name)
    except ValueError:     # policy.default refuses a value whose encoded words decode to a line break
        v = next((raw for key, raw in msg.raw_items() if key.lower() == name.lower()), None)
    return dec(v)


def python_reply_to_of(msg: AnyMessage) -> str:
    values = [str(re.sub(r"\r?\n[ \t]+", " ", v) if isinstance(v, str) else v)
              for name, v in msg.raw_items() if name.lower() == "reply-to"]
    return next((a.strip(".'") for _, a in getaddresses(values, strict=False) if "@" in a), "")


def python_decode(part: AnyMessage) -> str:
    try:
        payload = decoded(part)
        if payload is None:
            return ""
        return payload.decode(part.get_content_charset() or "utf-8", "replace")
    except Exception:
        return ""


def python_decode_fn(name: str | None) -> str | None:
    if not name:
        return None
    try:
        return "".join(
            (p.decode(e or "utf-8", "replace") if isinstance(p, bytes) else p)
            for p, e in decode_header(name))
    except Exception:
        return name


def python_parse(raw_bytes: bytes) -> Parsed:
    try:
        msg: AnyMessage = email.message_from_bytes(raw_bytes, policy=policy.default)
    except Exception:
        msg = email.message_from_bytes(raw_bytes or b"")
    text: str | None = None
    html: str | None = None
    cal: tuple[int, AnyMessage] | None = None
    attachments: list[Attachment] = []
    idx = 0
    for part in walk(msg):
        if part.get_content_maintype() == "multipart":
            if holds_no_parts(part) and text is None:
                text = python_decode(part)
            continue
        idx += 1
        ctype = (part.get_content_type() or "").lower()
        disp = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        cid = python_hdr(part, "Content-ID").strip("<>")
        is_attachment = disp == "attachment" or (filename and disp != "inline") or ctype == "message/rfc822"
        if is_attachment or (filename and not cid):
            payload = part_bytes(part)
            attachments.append({
                "part_id": str(idx),
                "filename": python_decode_fn(filename) or ("message.eml" if ctype == "message/rfc822" else f"part-{idx}"),
                "content_type": ctype, "size": len(payload),
                "content_id": cid or None, "is_inline": int(disp == "inline" or bool(cid)), "travels": travels(part),
            })
            continue
        if cid:
            payload = decoded(part) or b""
            attachments.append({
                "part_id": str(idx), "filename": python_decode_fn(filename) or f"inline-{idx}",
                "content_type": ctype, "size": len(payload),
                "content_id": cid, "is_inline": 1, "travels": False,
            })
            continue
        if ctype == "text/plain" and text is None:
            text = as_written(part, python_decode(part))
        elif ctype == "text/html" and html is None:
            html = python_decode(part)
        elif ctype == "text/calendar" and cal is None:
            cal = (idx, part)
    if cal and not any("calendar" in a["content_type"] or a["content_type"] == "application/ics"
                       or a["filename"].lower().endswith(".ics") for a in attachments):
        attachments.append({
            "part_id": str(cal[0]), "filename": "invite.ics", "content_type": "text/calendar",
            "size": len(decoded(cal[1]) or b""), "content_id": None, "is_inline": 1, "travels": False,
        })
    return {
        "text": text, "html": html, "attachments": attachments,
        "to": python_hdr(msg, "To"), "cc": python_hdr(msg, "Cc"),
        "reply_to": python_reply_to_of(msg), "date": python_hdr(msg, "Date"),
    }


def python_extract_part(raw_bytes: bytes, part_id: str | int) -> tuple[bytes, str] | tuple[None, None]:
    try:
        want = int(part_id)
    except (TypeError, ValueError):
        return None, None
    try:
        msg: AnyMessage = email.message_from_bytes(raw_bytes, policy=policy.default)
    except Exception:
        msg = email.message_from_bytes(raw_bytes or b"")
    idx = 0
    for part in walk(msg):
        if part.get_content_maintype() == "multipart":
            continue
        idx += 1
        if idx == want:
            return part_bytes(part), (part.get_content_type() or "application/octet-stream").lower()
    return None, None


def python_inline_parts(raw_bytes: bytes) -> dict[str, tuple[str, bytes]]:
    out: dict[str, tuple[str, bytes]] = {}
    try:
        msg = email.message_from_bytes(raw_bytes, policy=policy.default)
    except Exception:
        return out
    for part in walk(msg):
        if part.get_content_maintype() == "multipart":
            continue
        cid = python_hdr(part, "Content-ID").strip("<>")
        if cid:
            out[cid] = ((part.get_content_type() or "").lower(), decoded(part) or b"")
    return out


# ── what the two said of a message ────────────────────────────────────────────────────────────────────────────────

def read_with(parse, extract, inline, raw: bytes) -> dict:
    """Everything one side reads off `raw`: parse, every part extract_part numbers, and inline_parts."""
    parts, n = [], 1
    while (found := extract(raw, n)) != (None, None):
        parts.append(found)
        n += 1
    return {**parse(raw), "parts": parts, "inline": inline(raw)}


def core_reading(raw: bytes) -> dict:
    return read_with(mime.parse, mime.extract_part, mime.inline_parts, raw)


def readings(raw: bytes) -> tuple[dict, dict]:
    return read_with(python_parse, python_extract_part, python_inline_parts, raw), core_reading(raw)


def sent_as_is(old: dict, new: dict) -> None:
    """An attached email (message/rfc822, message/global, a delivery report) comes as it was sent, where Python wrote out
    again what it had parsed of it, with LF line ends and, for a delivery report, its first block alone. Its bytes and
    its size are the core's own, then, and must hold Python's. `old` takes them, so everything else must be equal."""
    if len(old["parts"]) != len(new["parts"]):
        return
    for at, (was, now) in enumerate(zip(old["parts"], new["parts"], strict=True)):
        if was[1] in ("message/rfc822", "message/global") and was[1] == now[1]:
            assert python_parse(now[0].replace(b"\r\n", b"\n")) == python_parse(was[0]), at      # the same email
            old["parts"][at] = now
        elif was[1].startswith("message/") and was[1] == now[1]:
            assert now[0].replace(b"\r\n", b"\n").startswith(was[0].strip(b"\n")), at
            old["parts"][at] = now
    for was, now in zip(old["attachments"], new["attachments"], strict=False):
        if was["content_type"].startswith("message/") and was == {**now, "size": was["size"]}:
            assert now["size"] == len(new["parts"][int(now["part_id"]) - 1][0])
            was["size"] = now["size"]


def assert_same(raw: bytes) -> None:
    old, new = readings(raw)
    sent_as_is(old, new)
    assert new == old


# ── generated messages ────────────────────────────────────────────────────────────────────────────────────────────
# Every charset here is one both read alike: iso-8859-1 and us-ascii without the bytes WHATWG reads as windows-1252,
# and none of big5, gb2312, euc-kr, shift_jis or euc-jp, whose tables differ (see the differences at the end).

CHARSETS = {None: "utf-8", "utf-8": "utf-8", "UTF-8": "utf-8", "iso-8859-1": "latin-1", "us-ascii": "ascii",
            "windows-1252": "cp1252", "iso-8859-2": "iso-8859-2", "koi8-r": "koi8-r", "windows-1251": "cp1251", "gbk": "gbk"}
WORDS = st.text(alphabet=st.characters(codec="utf-8", exclude_categories=("Cs", "Cc")), min_size=1, max_size=8)
LINES = st.lists(st.text(alphabet=st.characters(codec="utf-8", exclude_categories=("Cs",), exclude_characters="\r\n"),
                         max_size=30), max_size=5)


def text_in(charset: str | None, lines: list[str]) -> bytes:
    codec = CHARSETS[charset]
    kept = ["".join(c for c in line if c.encode(codec, "ignore") and not ("\x7f" < c < "\xa0")) for line in lines]
    return "\r\n".join(kept).encode(codec)


@st.composite
def encoded(draw, body: bytes) -> tuple[bytes, bytes]:
    """(Content-Transfer-Encoding header line, body as sent)."""
    cte = draw(st.sampled_from([None, "7bit", "8bit", "base64", "quoted-printable", "BASE64", "binary"]))
    if cte is None:
        return b"", body
    wire = body
    if cte.lower() == "base64":
        wire = base64.encodebytes(body).replace(b"\n", b"\r\n")
    elif cte == "quoted-printable":
        wire = quopri.encodestring(body.replace(b"\r\n", b"\n")).replace(b"\n", b"\r\n")
    return b"Content-Transfer-Encoding: " + cte.encode() + b"\r\n", wire


FILENAMES = st.sampled_from(["a.pdf", "swatch 2.png", "notes.txt", "résumé.pdf", "日本.txt", "invite.ics",
                             "x;y.pdf", "Q&A.doc"])


@st.composite
def file_name(draw, key: str) -> bytes:
    """`; key=...` as a sender writes it: a token, a quoted string, RFC 2231, or RFC 2047 encoded words."""
    name = draw(FILENAMES)
    how = draw(st.sampled_from(["quoted", "token", "2231", "2047q", "2047b"] if name.isascii() else ["2231", "2047q", "2047b"]))
    if how == "token" and all(c.isalnum() or c in ".-_" for c in name):
        return f"; {key}={name}".encode()
    if how in ("token", "quoted"):
        return f'; {key}="{name}"'.encode()
    if how == "2231":
        return f"; {key}*=utf-8''".encode() + "".join(
            c if c.isascii() and c.isalnum() else "".join(f"%{b:02X}" for b in c.encode()) for c in name).encode()
    if how == "2047b":
        return f'; {key}="=?utf-8?B?{base64.b64encode(name.encode()).decode()}?="'.encode()
    return f'; {key}="=?utf-8?Q?{"".join(c if c.isascii() and c.isalnum() else "".join(f"={b:02X}" for b in c.encode()) for c in name)}?="'.encode()


@st.composite
def leaf(draw) -> bytes:
    kind = draw(st.sampled_from(["text/plain", "text/plain", "text/html", "text/calendar", "application/pdf",
                                 "image/png", "application/octet-stream", "TEXT/PLAIN"]))
    charset = draw(st.sampled_from(list(CHARSETS))) if kind.lower().startswith("text/") else None
    head = b"Content-Type: " + kind.encode()
    if charset:
        head += b"; charset=" + draw(st.sampled_from([charset, f'"{charset}"'])).encode()
    lines = draw(LINES)
    if kind == "text/plain" and draw(st.booleans()):
        head += b"; format=flowed" + draw(st.sampled_from([b"", b"; delsp=yes", b"; delsp=no"]))
        lines = [line + draw(st.sampled_from(["", " "])) for line in lines]
    if draw(st.booleans()):
        head += draw(file_name("name"))
    head += b"\r\n"
    if (disposition := draw(st.sampled_from([None, "inline", "attachment", "ATTACHMENT"]))) is not None:
        head += b"Content-Disposition: " + disposition.encode() + (draw(file_name("filename")) if draw(st.booleans()) else b"") + b"\r\n"
    if draw(st.booleans()):
        head += b"Content-ID: <" + draw(st.sampled_from([b"p1@example.test", b"p2@example.test", b"logo"])) + b">\r\n"
    cte, wire = draw(encoded(text_in(charset, lines) if charset or kind.startswith("text") else bytes(draw(st.binary(max_size=40)))))
    return head + cte + b"\r\n" + wire


@st.composite
def multipart(draw, depth: int) -> bytes:
    kind = draw(st.sampled_from(["mixed", "alternative", "related", "MIXED", "report"]))
    boundary = draw(st.sampled_from(["b", "=_part_", "----=_NextPart_000", "a'()+_,-./:=?b"])) + f"{depth}"
    quoted = draw(st.sampled_from([f'"{boundary}"', boundary])) if boundary.isalnum() else f'"{boundary}"'
    parts = draw(st.lists(part(depth + 1), min_size=1, max_size=3))
    preamble = draw(st.sampled_from([b"", b"This is a multi-part message in MIME format.\r\n"]))
    epilogue = draw(st.sampled_from([b"", b"epilogue\r\n"]))
    body = preamble + b"".join(b"--" + boundary.encode() + b"\r\n" + p + b"\r\n" for p in parts)
    return (b"Content-Type: multipart/" + kind.encode() + b"; boundary=" + quoted.encode() + b"\r\n\r\n"
            + body + b"--" + boundary.encode() + b"--\r\n" + epilogue)


@st.composite
def attached(draw, depth: int) -> bytes:
    inner = b"From: Bo <bo@example.test>\r\nSubject: Inner\r\n" + draw(part(depth + 1))
    return b"Content-Type: message/rfc822\r\n" + draw(st.sampled_from([b"", b"Content-Disposition: attachment\r\n"])) + b"\r\n" + inner


def part(depth: int) -> st.SearchStrategy[bytes]:
    if depth >= 3:
        return leaf()
    return st.one_of(leaf(), leaf(), multipart(depth), attached(depth))


NAMES = st.sampled_from(["", "Ada Lin", "Lin, Bo", "Mr. Ed", "Jürgen", "=?utf-8?q?Caf=C3=A9?=", "=?utf-8?B?5pel5pys?=",
                         '"Quoted Name"', "O'Neil"])
DOMAINS = st.sampled_from(["example.test", "mail.example.test", "studio.example.test"])


@st.composite
def mailbox(draw) -> str:
    local = draw(st.sampled_from(["ada", "bo.lin", "x+tag", "desk", "Noreply"]))
    address = f"{local}@{draw(DOMAINS)}"
    name = draw(NAMES)
    if not name:
        return draw(st.sampled_from([address, f"<{address}>"]))
    if any(c in name for c in ",.") and not name.startswith("="):
        name = f'"{name}"'
    return f"{name} <{address}>"


@st.composite
def address_list(draw) -> str:
    entries = draw(st.lists(mailbox(), min_size=1, max_size=3))
    if draw(st.integers(0, 5)) == 0:
        entries.append("Team: " + ", ".join(draw(st.lists(mailbox(), max_size=2))) + ";")
    return draw(st.sampled_from([", ", ",\r\n "])).join(entries)


@st.composite
def dates(draw) -> str:
    when = draw(st.datetimes(min_value=datetime(1971, 1, 1), max_value=datetime(2099, 12, 31),
                             timezones=st.sampled_from([UTC, timezone(timedelta(hours=-8)), timezone(timedelta(hours=5, minutes=30))])))
    written = format_datetime(when)
    return draw(st.sampled_from([written, written.replace(" +0000", " GMT"), written.split(", ", 1)[1], written + " (UTC)"]))


@st.composite
def messages(draw) -> bytes:
    head = b"From: Ada Lin <ada@example.test>\r\nSubject: Swatches\r\nMIME-Version: 1.0\r\n"
    for name, value in (("To", address_list()), ("Cc", address_list()), ("Reply-To", address_list()), ("Date", dates())):
        if draw(st.booleans()):
            head += f"{name}: {draw(value)}\r\n".encode()
    return head + draw(part(0))


@st.composite
def broken(draw) -> bytes:
    """A generated message, then broken as mail breaks in transit or in a careless sender's hands."""
    raw = draw(messages())
    how = draw(st.sampled_from(["cut", "lf", "unclosed", "no-blank", "bad-base64", "byte", "dash-line"]))
    if how == "cut":
        return raw[:draw(st.integers(0, len(raw)))]
    if how == "lf":
        return raw.replace(b"\r\n", b"\n")
    if how == "unclosed":
        return re.sub(rb"\r\n--[^\r\n]*--\r\n", b"\r\n", raw, count=draw(st.integers(1, 3)))
    if how == "no-blank":
        return re.sub(rb"(Content-Type: [^\r\n]*\r\n)\r\n", rb"\1", raw, count=draw(st.integers(1, 3)))
    if how == "bad-base64":
        return re.sub(rb"(base64\r\n\r\n[A-Za-z0-9+/]{2})", rb"\1*=", raw, flags=re.I)
    if how == "dash-line":
        at = draw(st.integers(0, len(raw)))
        return raw[:at] + b"\r\n--\r\n" + raw[at:]
    at = draw(st.integers(0, len(raw)))
    return raw[:at] + bytes([draw(st.integers(0, 255).filter(lambda b: b not in b"\r\n"))]) + raw[at:]


# ── hand-made messages, many of them malformed ────────────────────────────────────────────────────────────────────

H = (b"From: Ada Lin <ada@example.test>\r\nTo: bo@example.test\r\nSubject: Swatches\r\n"
     b"Date: Mon, 2 Nov 2026 12:00:00 +0000\r\nMIME-Version: 1.0\r\n")
INNER = (b"From: Bo <bo@example.test>\r\nSubject: Inner\r\nContent-Type: multipart/mixed; boundary=in\r\n\r\n--in\r\n"
         b"Content-Type: text/plain\r\n\r\ninner text\r\n--in\r\nContent-Type: application/pdf; name=in.pdf\r\n\r\n%PDF\r\n--in--\r\n")


def mixed(*parts: bytes, boundary: bytes = b"b", ctype: bytes = b"multipart/mixed") -> bytes:
    body = b"".join(b"--" + boundary + b"\r\n" + p + b"\r\n" for p in parts) + b"--" + boundary + b"--\r\n"
    return H + b"Content-Type: " + ctype + b'; boundary="' + boundary + b'"\r\n\r\n' + body


def text(body: bytes, ctype: bytes = b"text/plain; charset=utf-8") -> bytes:
    return b"Content-Type: " + ctype + b"\r\n\r\n" + body


def headers(*lines: bytes) -> bytes:
    return b"From: a@example.test\r\n" + b"".join(line + b"\r\n" for line in lines) + b"Subject: s\r\n\r\nhi\r\n"


def hand_made() -> dict[str, bytes]:
    cases: dict[str, bytes] = {}
    for i, (label, body) in enumerate([
        (b"iso-8859-1", b"caf\xe9 \x80 \x93quoted\x94"), (b"us-ascii", b"caf\xe9"), (b"windows-1252", b"\x93hi\x94 \x80"),
        (b"x-unknown-charset", b"caf\xc3\xa9"), (b"x-unknown-charset", b"caf\xe9"), (b"gb2312", "\u4f60\u597d".encode("gbk")),
        (b"gb2312", b"\x81\x40"), (b"shift_jis", "\u3053\u3093\u306b\u3061\u306f".encode("shift_jis")),
        (b"shift_jis", "\u301c".encode("shift_jis")), (b"euc-kr", "\uc548\ub155".encode("euc-kr")),
        (b"utf-16", "hello \u00e9".encode("utf-16")), (b"utf-7", "hi \u00e9".encode("utf-7")),
        (b"iso-8859-8-i", "\u05e9\u05dc\u05d5\u05dd".encode("iso-8859-8")), (b"koi8-r", "\u043f\u0440\u0438".encode("koi8-r")),
        (b"utf-8", b"\xef\xbb\xbfBOM first"), (b"utf-8", b"bad \xff\xfe utf8 \xe2\x82"), (b"UTF8", "\u00e9".encode()),
        (b"latin_1", b"caf\xe9"), (b"ks_c_5601-1987", "\uc548\ub155".encode("cp949")), (b"iso-8859-15", b"\xa4 euro"),
        (b"windows-1251", "\u043f\u0440\u0438".encode("cp1251")), (b"iso-2022-jp", "\u3053\u3093".encode("iso-2022-jp")),
        (b"big5", "\u4f60\u597d".encode("big5")), (b"euc-jp", "\u3053\u3093".encode("euc-jp")), (b"tis-620", "\u0e2a".encode("tis-620")),
        (b"iso-8859-9", b"\xd0\xdd"), (b"cp1252", b"\x80"), (b"", b"no charset \xc3\xa9"), (b'"utf-8"', "\u00e9".encode()),
        (b"unknown-8bit", b"caf\xe9"), (b"utf-8-sig", b"\xef\xbb\xbfx"), (b"macintosh", b"\x8e"),
    ]):
        cases[f"charset-{i}"] = H + b"Content-Type: text/plain; charset=" + label + b"\r\n\r\n" + body + b"\r\n"
    cases["charset-html-latin1"] = H + b"Content-Type: text/html; charset=iso-8859-1\r\n\r\n<p>caf\xe9</p>\r\n"

    b64 = base64.encodebytes("Gr\u00fc\u00dfe aus K\u00f6ln\n".encode())
    for name, cte, body in [
        ("b64-ok", b"base64", b64), ("b64-badchars", b"base64", b"R3L*Dn+fZSBh#dXMgS8O2bG4K\r\n"),
        ("b64-nopad", b"base64", b"aGVsbG8"), ("b64-len1", b"base64", b"aGVsbG8gd"), ("b64-midpad", b"base64", b"aGk=aGk=\r\n"),
        ("b64-spaces", b"base64", b"aGVs bG8g\r\n d29y bGQ=\r\n"), ("b64-upper", b"BASE64", b"aGVsbG8="),
        ("b64-comment", b"base64 (encoded)", b"aGVsbG8="), ("b64-trailing-space", b"base64  ", b"aGVsbG8="),
        ("qp-ok", b"quoted-printable", b"caf=C3=A9 soft=\r\nbreak\r\n"), ("qp-lower", b"quoted-printable", b"caf=c3=a9\r\n"),
        ("qp-bad", b"quoted-printable", b"100=ZZ and =4 and =\r\n"), ("qp-trailing", b"quoted-printable", b"line   \r\nnext\t\r\n"),
        ("qp-lf", b"quoted-printable", b"soft=\nbreak\n=3D\n"), ("qp-eq-end", b"quoted-printable", b"ends with ="),
        ("unknown", b"x-foo", b"as it came\r\n"), ("8bit", b"8bit", b"caf\xc3\xa9\r\n"), ("binary", b"binary", b"\x00\x01text\r\n"),
        ("uu", b"x-uuencode", b"begin 644 a.txt\r\n%:&5L;&\\*\r\n`\r\nend\r\n"), ("uu-nobegin", b"x-uuencode", b"nothing here\r\n"),
        ("nested-comments", b"(" * 3000 + b"base64", b"SGk="),
    ]:
        cases["cte-" + name] = H + b"Content-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: " + cte + b"\r\n\r\n" + body
        cases["cte-att-" + name] = mixed(text(b"hi"), b"Content-Type: application/octet-stream; name=a.bin\r\n"
                                         b"Content-Transfer-Encoding: " + cte + b"\r\n\r\n" + body)

    cases["flowed-delsp"] = H + b"Content-Type: text/plain; format=flowed; delsp=yes\r\n\r\nlongw \r\nord.\r\n> q \r\n> on\r\n-- \r\nBo\r\n"
    cases["flowed-quoted-params"] = H + b'Content-Type: text/plain; format="Flowed"; DelSp="No"\r\n\r\nsoft \r\nbreak\r\n'
    cases["flowed-qp"] = H + b"Content-Type: text/plain; format=flowed\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\nsoft =\r\nbreak \r\nhere\r\n"

    cases["rfc822-plain"] = mixed(text(b"see attached"), b"Content-Type: message/rfc822\r\n\r\n" + INNER)
    cases["rfc822-named"] = mixed(text(b"see attached"), b"Content-Type: message/rfc822; name=fwd.eml\r\n"
                                  b"Content-Disposition: attachment; filename=fwd.eml\r\n\r\n" + INNER)
    cases["rfc822-base64"] = mixed(text(b"see"), b"Content-Type: message/rfc822\r\nContent-Transfer-Encoding: base64\r\n\r\n" + base64.encodebytes(INNER))
    cases["rfc822-only"] = H + b"Content-Type: message/rfc822\r\n\r\n" + INNER
    cases["rfc822-empty"] = mixed(text(b"x"), b"Content-Type: message/rfc822\r\n\r\n")
    cases["rfc822-cid"] = mixed(text(b"x"), b"Content-Type: message/rfc822\r\nContent-ID: <m@example.test>\r\n\r\n" + INNER)
    cases["message-global"] = mixed(text(b"x"), b"Content-Type: message/global\r\n\r\n" + INNER)
    cases["delivery-status"] = (H + b"Content-Type: multipart/report; report-type=delivery-status; boundary=r\r\n\r\n--r\r\n"
                                b"Content-Type: text/plain\r\n\r\nIt bounced.\r\n--r\r\nContent-Type: message/delivery-status\r\n\r\n"
                                b"Reporting-MTA: dns; mx.example.test\r\n\r\nFinal-Recipient: rfc822; x@example.test\r\nStatus: 5.1.1\r\n"
                                b"--r\r\nContent-Type: message/rfc822-headers\r\n\r\nFrom: a@example.test\r\nSubject: x\r\n--r--\r\n")
    cases["digest"] = (H + b"Content-Type: multipart/digest; boundary=d\r\n\r\n--d\r\n\r\nFrom: a@example.test\r\nSubject: one\r\n\r\n"
                       b"first\r\n--d\r\n\r\nFrom: b@example.test\r\nSubject: two\r\n\r\nsecond\r\n--d--\r\n")
    cases["deep"] = (H + b"Content-Type: multipart/mixed; boundary=a\r\n\r\n--a\r\nContent-Type: multipart/related; boundary=b\r\n\r\n"
                     b"--b\r\nContent-Type: multipart/alternative; boundary=c\r\n\r\n--c\r\nContent-Type: text/plain\r\n\r\nplain\r\n"
                     b"--c\r\nContent-Type: text/html\r\n\r\n<p>html <img src=cid:p1></p>\r\n--c--\r\n--b\r\nContent-Type: image/png\r\n"
                     b"Content-ID: <p1>\r\n\r\nPNG\r\n--b--\r\n--a\r\nContent-Type: application/pdf; name=x.pdf\r\n\r\n%PDF\r\n--a--\r\n")
    cases["same-boundary-nested"] = (H + b"Content-Type: multipart/mixed; boundary=a\r\n\r\n--a\r\nContent-Type: multipart/alternative; "
                                     b"boundary=a\r\n\r\n--a\r\nContent-Type: text/plain\r\n\r\nplain\r\n--a--\r\n--a\r\n"
                                     b"Content-Type: text/html\r\n\r\n<p>h</p>\r\n--a--\r\n")
    cases["calendar-outlook"] = mixed(text(b"invite"), b"Content-Type: text/calendar; method=REQUEST; charset=utf-8\r\n"
                                      b"Content-Transfer-Encoding: base64\r\n\r\n" + base64.encodebytes(b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"),
                                      ctype=b"multipart/alternative")
    cases["calendar-and-ics"] = mixed(text(b"invite"), b"Content-Type: text/calendar\r\n\r\nBEGIN:VCALENDAR\r\nEND:VCALENDAR",
                                      b"Content-Type: application/ics; name=invite.ics\r\nContent-Disposition: attachment; "
                                      b"filename=invite.ics\r\n\r\nBEGIN:VCALENDAR\r\nEND:VCALENDAR")

    for name, hdrs in [
        ("rfc2231", b"Content-Disposition: attachment; filename*=utf-8''r%C3%A9sum%C3%A9.pdf\r\n"),
        ("rfc2231-lang", b"Content-Disposition: attachment; filename*=iso-8859-1'fr'r%E9sum%E9.pdf\r\n"),
        ("rfc2231-cont", b"Content-Disposition: attachment;\r\n filename*0*=utf-8''long%20na;\r\n filename*1*=me%C3%A9.pdf\r\n"),
        ("rfc2231-cont-plain", b"Content-Disposition: attachment; filename*0=\"long \"; filename*1=\"name.pdf\"\r\n"),
        ("ew-quoted", b'Content-Disposition: attachment; filename="=?utf-8?B?csOpc3Vtw6kucGRm?="\r\n'),
        ("ew-unquoted", b"Content-Disposition: attachment; filename==?utf-8?Q?r=C3=A9sum=C3=A9.pdf?=\r\n"),
        ("ew-split", b'Content-Disposition: attachment; filename="=?utf-8?Q?r=C3=A9?= =?utf-8?Q?sum=C3=A9.pdf?="\r\n'),
        ("ew-name", b'Content-Type: application/pdf; name="=?iso-8859-1?Q?r=E9sum=E9.pdf?="\r\n'),
        ("ew-badcharset", b'Content-Disposition: attachment; filename="=?x-bogus?Q?r=E9.pdf?="\r\n'),
        ("ew-broken-b64", b'Content-Disposition: attachment; filename="=?utf-8?B?cmVzdW1*?=.pdf"\r\n'),
        ("name-only", b"Content-Type: application/pdf; name=report.pdf\r\n"),
        ("both", b"Content-Type: application/pdf; name=a.pdf\r\nContent-Disposition: attachment; filename=b.pdf\r\n"),
        ("empty-filename", b'Content-Type: application/pdf; name=a.pdf\r\nContent-Disposition: attachment; filename=""\r\n'),
        ("spaces", b'Content-Disposition: attachment; filename="  my file .pdf  "\r\n'),
        ("path", b'Content-Disposition: attachment; filename="C:\\\\Users\\\\x\\\\a.pdf"\r\n'),
        ("raw-utf8", "Content-Disposition: attachment; filename=\"r\u00e9sum\u00e9.pdf\"\r\n".encode()),
        ("raw-latin1", b"Content-Disposition: attachment; filename=\"r\xe9sum\xe9.pdf\"\r\n"),
        ("inline-named", b"Content-Disposition: inline; filename=pic.png\r\n"),
        ("dup-disposition", b"Content-Disposition: inline; filename=a.png\r\nContent-Disposition: attachment; filename=b.png\r\n"),
        ("disp-nosemi", b"Content-Disposition: attachment filename=a.pdf\r\n"),
        ("disp-upper", b"Content-Disposition: ATTACHMENT; FILENAME=A.PDF\r\n"),
        ("disp-comment", b"Content-Disposition: attachment (file); filename=a.pdf\r\n"),
        ("disp-folded", b"Content-Disposition: attachment;\r\n\tfilename=\"folded\r\n name.pdf\"\r\n"),
        ("disp-other", b"Content-Disposition: form-data; name=field; filename=f.txt\r\n"),
    ]:
        typed = hdrs if b"Content-Type" in hdrs else b"Content-Type: application/octet-stream\r\n" + hdrs
        cases["fn-" + name] = mixed(text(b"see"), typed + b"\r\nDATA")

    for name, cid in [("plain", b"<a@example.test>"), ("bare", b"a@example.test"), ("spaced", b"< a@example.test >"),
                      ("double", b"<<a@example.test>>"), ("empty", b""), ("brackets", b"<>"), ("folded", b"\r\n <a@example.test>"),
                      ("trailing", b"<a@example.test>  "), ("ew", b"=?utf-8?q?a=40example.test?="), ("dup", b"<a@x>\r\nContent-ID: <b@x>")]:
        cases["cid-" + name] = mixed(text(b"<img src=cid:a@example.test>", b"text/html"), b"Content-Type: image/png\r\nContent-ID: " + cid + b"\r\n\r\nPNG")
    cases["cid-twice"] = mixed(text(b"x", b"text/html"), b"Content-Type: image/png\r\nContent-ID: <same>\r\n\r\nONE",
                               b"Content-Type: image/gif\r\nContent-ID: <same>\r\n\r\nTWO")
    cases["cid-attachment"] = mixed(text(b"x"), b"Content-Type: image/png; name=p.png\r\nContent-ID: <p>\r\nContent-Disposition: attachment\r\n"
                                    b"Content-Transfer-Encoding: base64\r\n\r\niVBORw0KGgo=")

    multi = H + b"Content-Type: multipart/mixed; boundary=b\r\n\r\n"
    cases.update({
        "no-boundary": H + b"Content-Type: multipart/mixed\r\n\r\nplain body of a multipart\r\n",
        "no-boundary-nested": mixed(text(b"first"), b"Content-Type: multipart/alternative\r\n\r\ninner text"),
        "no-boundary-cte": H + b"Content-Type: multipart/mixed; charset=iso-8859-1\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\ncaf=E9\r\n",
        "boundary-missing": H + b"Content-Type: multipart/mixed; boundary=zzz\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n--b--\r\n",
        "boundary-unclosed": multi + b"--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n--b\r\nContent-Type: application/pdf; name=a.pdf\r\n\r\n%PDF\r\n",
        "boundary-close-first": multi + b"preamble text\r\n--b--\r\nepilogue\r\n",
        "boundary-close-first-lf": (multi + b"preamble text\r\n--b--\r\n").replace(b"\r\n", b"\n"),
        "boundary-consecutive": multi + b"--b\r\n--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n--b\r\n--b\r\nContent-Type: application/pdf; "
                                        b"name=a.pdf\r\n\r\n%PDF\r\n--b--\r\n",
        "boundary-blank-part": multi + b"--b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n--b--\r\n",
        "boundary-trailing-ws": multi + b"--b  \r\nContent-Type: text/plain\r\n\r\nhi\r\n--b--  \r\n",
        "boundary-trailing-garbage": multi + b"--b garbage\r\nContent-Type: text/plain\r\n\r\nhi\r\n--b--\r\n",
        "boundary-prefix": multi + b"--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n--bb\r\nmore\r\n--b--\r\n",
        "boundary-midline": multi + b"text --b\r\nContent-Type: text/plain\r\n\r\nhi\r\n--b--\r\n",
        "boundary-preamble-epilogue": multi + b"This is a preamble.\r\n--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n--b--\r\nepilogue here\r\n",
        "boundary-spaces": H + b'Content-Type: multipart/mixed; boundary="a b c"\r\n\r\n--a b c\r\nContent-Type: text/plain\r\n\r\nhi\r\n--a b c--\r\n',
        "boundary-empty": H + b'Content-Type: multipart/mixed; boundary=""\r\n\r\n--\r\nContent-Type: text/plain\r\n\r\nhi\r\n----\r\n',
        "boundary-empty-unquoted": H + b"Content-Type: multipart/mixed; boundary=\r\n\r\n--\r\nContent-Type: text/plain\r\n\r\nhi\r\n----\r\n",
        "boundary-empty-bare": H + b"Content-Type: multipart/mixed; boundary\r\n\r\n--\r\nContent-Type: text/plain\r\n\r\nhi\r\n----\r\n",
        "boundary-empty-bare-last": H + b"Content-Type: multipart/mixed; charset=x; boundary \r\n\r\npre\r\n--\r\n\r\nhi\r\n",
        "boundary-empty-ends-header": H + b'Content-Type: multipart/alternative; boundary\r\n--\r\n="=_part_0"\r\n\r\n'
                                      b"--=_part_0\r\nContent-Type: text/plain\r\n\r\n\r\n--=_part_0--\r\n",
        "boundary-empty-nested": mixed(text(b"first"), b'Content-Type: multipart/alternative; boundary=""\r\n\r\npre\r\n--\r\n'
                                       b"Content-Type: text/html\r\n\r\n<b>x</b>\r\n-- \r\nBo\r\n---\r\n----"),
        "boundary-lf": (multi + b"--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n--b\r\nContent-Type: application/pdf; name=a.pdf\r\n\r\n"
                        b"%PDF\r\n--b--\r\n").replace(b"\r\n", b"\n"),
        "boundary-cr": (multi + b"--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n--b--\r\n").replace(b"\r\n", b"\r"),
        "boundary-outer-ends-inner": H + b"Content-Type: multipart/mixed; boundary=a\r\n\r\n--a\r\nContent-Type: multipart/alternative; "
                                         b"boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nplain\r\n--a\r\n"
                                         b"Content-Type: application/pdf; name=a.pdf\r\n\r\n%PDF\r\n--a--\r\n",
        "boundary-cut-in-a-part": multi + b"--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n--b\r\nContent-Ty",
        "boundary-cut-at-a-delimiter": multi + b"--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n--b",
        "multipart-empty-parts": multi + b"--b\r\n--b--\r\n",
        "part-no-ctype": mixed(b"\r\njust text", b"Content-Disposition: attachment\r\n\r\nfile"),
        "part-no-headers-no-blank": mixed(b"text with no blank line"),
        "part-bad-ctype": mixed(b"Content-Type: garbage\r\n\r\nwhat", b"Content-Type: text\r\n\r\nno subtype",
                                b"Content-Type: text/plain/extra\r\n\r\nthree", b"Content-Type: TEXT/HTML\r\n\r\n<b>upper</b>",
                                b"Content-Type: application/\r\n\r\ncut short"),
        "part-ctype-comment": mixed(b"Content-Type: text/html (comment); charset=utf-8\r\n\r\n<b>c</b>"),
        "ctype-multipart-no-subtype": H + b"Content-Type: multipart; boundary=b\r\n\r\n--b\r\nContent-Type: text/html\r\n\r\n<b>x</b>\r\n--b--\r\n",
        "ctype-multipart-two-slashes": H + b"Content-Type: multipart/mixed/x; boundary=b\r\n\r\n--b\r\n\r\nx\r\n--b--\r\n",
        "ctype-message-no-subtype": mixed(text(b"x"), b"Content-Type: message\r\n\r\n" + INNER),
        "ctype-message-empty-subtype": mixed(text(b"x"), b"Content-Type: message/\r\n\r\nFrom: a@example.test\r\n\r\nhi"),
        "ctype-dup": H +b"Content-Type: text/html\r\nContent-Type: text/plain\r\n\r\n<b>which</b>\r\n",
        "ctype-dup-multipart": H + b"Content-Type: text/plain\r\nContent-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\n"
                                   b"Content-Type: text/plain\r\n\r\nhi\r\n--b--\r\n",
        "cte-dup": H + b"Content-Type: text/plain\r\nContent-Transfer-Encoding: base64\r\nContent-Transfer-Encoding: 7bit\r\n\r\naGk=\r\n",
        "no-ctype": b"From: a@example.test\r\nSubject: x\r\n\r\nbody\r\n",
        "empty": b"",
        "only-body": b"no headers here at all\r\njust text\r\n",
        "only-headers": b"From: a@example.test\r\nSubject: x\r\n",
        "only-headers-blank": b"From: a@example.test\r\nSubject: x\r\n\r\n",
        "mbox-from": b"From ada@example.test Mon Nov  2 12:00:00 2026\r\nFrom: a@example.test\r\nSubject: x\r\n\r\nbody\r\n",
        "lf-only": (H + b"Content-Type: text/plain\r\n\r\nline one\r\nline two\r\n").replace(b"\r\n", b"\n"),
        "cr-only": (H + b"Content-Type: text/plain\r\n\r\nline one\r\nline two\r\n").replace(b"\r\n", b"\r"),
        "header-space-colon": b"From: a@example.test\r\nSubject : spaced\r\nContent-Type: text/html\r\n\r\n<b>x</b>\r\n",
        "header-no-colon": b"From: a@example.test\r\nnot a header line\r\nContent-Type: text/html\r\n\r\n<b>x</b>\r\n",
        "header-body-without-blank": b"From: a@example.test\r\nSubject: s\r\nHello there\r\n\r\nsecond paragraph\r\n",
        "header-lone-cr": b"From: a@example.test\rTo: bo@example.test\r\nSubject: s\r\n\r\nhi\r\n",
        "leading-blank": b"\r\nFrom: a@example.test\r\n\r\nbody\r\n",
        "leading-space": b" folded first\r\nFrom: a@example.test\r\n\r\nbody\r\n",
        "nul-bytes": H + b"Content-Type: text/plain\r\n\r\nnul\x00inside\r\n",
    })

    for i, to in enumerate([
        b'"Lin, Bo" <bo@example.test>, ada@example.test', b"=?utf-8?q?Caf=C3=A9?= <x@example.test>",
        b"(comment) y@example.test (c2)", b"bo@example.test (Bo Lin)", b"Bo  Lin <bo@example.test>", b'"Bo  Lin" <bo@example.test>',
        b"undisclosed-recipients:;", b"Team: a@example.test, b@example.test;", b"bo@example.test,\r\n ada@example.test",
        "J\u00fcrgen <j@example.test>".encode(), b"J\xfcrgen <j@example.test>", b"=?utf-8?q?Bo=0D=0AX?= <bo@example.test>",
        b"=?utf-8?q?Bo?= =?utf-8?q?_Lin?= <bo@example.test>", b"=?x-bogus?q?Bo?= <bo@example.test>", b"=?utf-8?B?Qm8*?= <bo@example.test>",
        b"=?utf-8?q?caf=E9?= <bo@example.test>", b"Bo.Lin <bo@example.test>", b'"Bo \\"the\\" Lin" <bo@example.test>',
        b"<bo@example.test>", b"bo", b"", b"   ", b'"quoted local"@example.test', b"Bo <bo@[192.0.2.1]>", b"bo@example.test,, ada@example.test",
        b"Bo Lin bo@example.test", b"<>", b"Bo <>", b"a@example.test, Team:;, b@example.test", b"Bo <bo@example.test", b"Mr. Bo <bo@example.test>",
        b"=?utf-8?q?Lin=2C_Bo?= <bo@example.test>", b"Bo\r\n Lin <bo@example.test>", b"@example.test", b"bo@", b"Bo <bo@ example.test>",
        b"\"Bo\" <bo@example.test>, \"\" <ada@example.test>", b"<@route:bo@example.test>", b"Bo (Lin) <bo@example.test>",
    ]):
        cases[f"to-{i:02d}"] = headers(b"To: " + to, b"Cc: " + to)
    cases["to-twice"] = headers(b"To: first@example.test", b"To: second@example.test")

    for i, rt in enumerate([b"desk@other.test", b"Help Desk <desk@other.test>", b'"boss@bank.test" <desk@other.test>',
                            b"=?utf-8?q?boss=40bank=2Etest?= <desk@other.test>", b"first@other.test, second@else.test",
                            b"Desk@Other.test", b"", b"no address here", b"<>", b"'desk@other.test'", b"desk@other.test.",
                            b"Team: desk@other.test;", b"desk", b"desk@other.test (Desk)", b"Bo Lin bo@example.test",
                            b"<desk@other.test", b"x <y> desk@other.test"]):
        cases[f"replyto-{i:02d}"] = headers(b"Reply-To: " + rt)
    cases["replyto-twice"] = headers(b"Reply-To: no address", b"Reply-To: second@example.test")

    for i, d in enumerate([b"Mon, 2 Nov 2026 12:00:00 +0000", b"2 Nov 2026 12:00 GMT", b"Mon, 02 Nov 2026 12:00:00 -0000",
                           b"Mon, 2 Nov 2026 12:00:00 +0530 (IST)", b"Mon, 2 Nov 26 12:00:00 EST", b"Mon, 2 Nov 99 12:00:00 PDT",
                           b"garbage date", b"", b"Mon,2 Nov 2026 12:00:00 +0000", b"Nov 2 2026 12:00:00 +0000", b"2-Nov-2026 12:00:00 +0000",
                           b"Mon, 2 Nov 2026 12.00.00 +0000", b"Mon, 2 Nov 2026 12:00:00+0100", b"Mon, 2 Nov 2026 12:00:60 +0000",
                           b"Mon, 31 Feb 2026 12:00:00 +0000", b"Mon, 2 Nov 2026 12:00:00 +2500", b"Mon, 2 Nov 2026 12:00:00 XYZ",
                           b"Mon, 2 Nov 2026 12:00:00", b"Mon, 2 Nov 2026 25:00:00 +0000", b"Thu, 1 January 1970 00:00:00 +0000",
                           b"Mon, 2 Nov 2026 12:00:00 +0099", b"Monday, 2 Nov 2026 12:00:00 +0000", b"mon, 2 nov 2026 12:00:00 utc",
                           b"Mon, 2 Nov 2026\r\n 12:00:00 +0000", b"=?utf-8?q?not_a_date?=", b"2026-11-02T12:00:00Z",
                           b"Mon, 2 Nov 0 12:00:00 +0000", b"Mon, 2 Nov 2026 12:00:00 -0800", b"Mon, 29 Feb 2024 23:59:59 +1400"]):
        cases[f"date-{i:02d}"] = headers(b"Date: " + d)
    cases["date-twice"] = headers(b"Date: Mon, 2 Nov 2026 12:00:00 +0000", b"Date: Tue, 3 Nov 2026 12:00:00 +0000")
    return cases


HAND_MADE = hand_made()

# Each hand-made message the core reads otherwise than Python did, by the test at the end that says how and why.
CHANGED = {
    **dict.fromkeys(["charset-0", "charset-1", "charset-6", "charset-8"], "a_charset_reads_as_a_browser_reads_it"),
    **dict.fromkeys(["charset-3", "charset-4", "charset-29"], "a_charset_no_one_knows_reads_as_utf8"),
    **dict.fromkeys(["cte-b64-len1", "cte-att-b64-len1", "cte-b64-midpad", "cte-att-b64-midpad"], "base64_decodes_what_it_can"),
    **dict.fromkeys(["cte-nested-comments", "cte-att-nested-comments"], "where_python_failed_the_core_answers"),
    "rfc822-base64": "an_attached_email_comes_as_it_was_sent",
    **dict.fromkeys(["same-boundary-nested", "boundary-midline", "boundary-trailing-garbage"],
                    "a_line_that_holds_a_delimiter_delimits"),
    "cr-only": "lines_that_end_in_cr_alone_end_in_crlf",
    **dict.fromkeys(["fn-empty-filename", "fn-ew-broken-b64", "fn-ew-unquoted"], "a_file_name_reads_as_written"),
    "part-ctype-comment": "a_type_with_a_comment_is_that_type",
    **dict.fromkeys(["to-02", "to-03", "to-12", "to-14", "to-33", "to-34", "to-38"], "an_address_shows_what_was_written"),
}


# ── the same answers ──────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(os.listdir(CORPUS)))
def test_the_corpus_reads_as_python_read_it(name):
    with open(os.path.join(CORPUS, name), "rb") as f:
        assert_same(f.read())


@pytest.mark.parametrize("name", sorted(set(HAND_MADE) - set(CHANGED)))
def test_hand_made_messages_read_as_python_read_them(name):
    assert_same(HAND_MADE[name])


@pytest.mark.parametrize("name", sorted(CHANGED))
def test_a_hand_made_message_named_as_changed_reads_otherwise(name):
    """So that CHANGED names only messages the two read otherwise, each one accounted for by a test at the end."""
    try:
        old, new = readings(HAND_MADE[name])
    except Exception:
        assert CHANGED[name] == "where_python_failed_the_core_answers"
        return
    try:
        sent_as_is(old, new)
    except AssertionError:
        assert CHANGED[name] == "an_attached_email_comes_as_it_was_sent"
        return
    assert new != old


@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(messages())
def test_generated_messages_read_as_python_read_them(raw):
    assert_same(raw)


@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(messages().map(lambda raw: raw.replace(b"\r\n", b"\n")))
def test_generated_messages_with_lf_line_ends_read_as_python_read_them(raw):
    assert_same(raw)


@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(broken())
def test_a_broken_message_loses_no_part_python_found(raw):
    """Broken mail is where the two parsers' guesses part, and where the differences at the end come from: a base64
    body cut mid-character decodes to other garbage, a delimiter with junk after it opens a part for the core. So
    this holds what matters: the core answers whatever comes, what it lists it hands over whole, and it finds every
    part Python found, with the same bytes where nothing had to be guessed."""
    new = core_reading(raw)
    parts = new["parts"]
    for a in new["attachments"]:
        assert parts[int(a["part_id"]) - 1][1] == a["content_type"]
        assert len(parts[int(a["part_id"]) - 1][0]) == a["size"]
    for cid, (content_type, data) in new["inline"].items():
        assert content_type.startswith("message/") or (data, content_type) in parts, cid
    try:
        old = python_parse(raw)
        old_parts = [found for n in range(1, len(parts) + 2) if (found := python_extract_part(raw, n)) != (None, None)]
    except Exception:
        return                                  # Python failed on it: see test_where_python_failed_the_core_answers
    assert len(parts) >= len(old_parts)
    if [t for _, t in parts] == [t for _, t in old_parts]:
        assert (new["text"] is None, new["html"] is None) == (old["text"] is None, old["html"] is None)
        if b"base64" not in raw.lower():
            assert [p for p in parts if not p[1].startswith("message/")] == [p for p in old_parts if not str(p[1]).startswith("message/")]


# ── where the core answers otherwise, on purpose ──────────────────────────────────────────────────────────────────
# Each holds for mail read from now on. What a sync stored stays as it was stored.

def test_each_change_is_a_test_below():
    assert {f"test_{why}" for why in CHANGED.values()} <= set(globals())


def test_a_charset_reads_as_a_browser_reads_it():
    """A charset label means what the WHATWG Encoding Standard says, as in every browser, where Python took it at its
    word. Senders label windows-1252 text iso-8859-1 or us-ascii all the time, and the C1 controls Python showed for
    bytes 0x80 to 0x9f were never meant: they are the euro sign and curly quotes. Likewise gb2312 reads as GBK and
    euc-kr as Windows' code page 949, the supersets their senders write, and Shift_JIS, EUC-JP and Big5 by the tables
    browsers use."""
    def text(charset: bytes, body: bytes) -> tuple[str | None, str | None]:
        raw = H + b"Content-Type: text/plain; charset=" + charset + b"\r\n\r\n" + body
        return mime.parse(raw)["text"], python_parse(raw)["text"]
    assert text(b"iso-8859-1", b"caf\xe9 \x80 \x93quoted\x94") == ("café € “quoted”", "café \x80 \x93quoted\x94")
    assert mime.parse(H + b"Content-Type: text/html; charset=iso-8859-1\r\n\r\n<p>\x93q\x94</p>")["html"] == "<p>“q”</p>"
    assert text(b"us-ascii", b"caf\xe9") == ("café", "caf�")
    assert text(b"gb2312", b"\x81\x40") == ("丂", "�@")                # outside gb2312, inside GBK
    assert text(b"gb2312", b"\xa1\xaa") == ("—", "―")
    assert text(b"euc-kr", "똠".encode("cp949")) == ("똠", "�c")   # a syllable only code page 949 holds
    assert text(b"shift_jis", b"\x81\x60") == ("～", "〜")
    assert text(b"euc-jp", b"\xa1\xc1") == ("～", "〜")
    assert text(b"big5", b"\xa2\x46") == ("￠", "¢")


def test_a_charset_no_one_knows_reads_as_utf8():
    """Python read a body in a charset it didn't know as no text at all. The core reads it as UTF-8, a byte that isn't
    UTF-8 replaced, so the reader sees what can be seen."""
    assert python_parse(HAND_MADE["charset-3"])["text"] == ""
    assert mime.parse(HAND_MADE["charset-3"])["text"] == "café\r\n"
    assert mime.parse(HAND_MADE["charset-4"])["text"] == "caf�\r\n"
    assert mime.parse(HAND_MADE["charset-29"])["text"] == "caf�\r\n"                # unknown-8bit


def test_base64_decodes_what_it_can():
    """Base64 with one character too many came from Python still encoded, and `=` inside it came out as control
    characters. The core decodes what it can, as mail clients do."""
    assert python_extract_part(HAND_MADE["cte-b64-len1"], 1) == (b"aGVsbG8gd", "text/plain")
    assert mime.extract_part(HAND_MADE["cte-b64-len1"], 1) == (b"hello ", "text/plain")
    assert mime.extract_part(HAND_MADE["cte-att-b64-len1"], 2) == (b"hello ", "application/octet-stream")
    assert python_extract_part(HAND_MADE["cte-b64-midpad"], 1) == (b"hi\x1a\x1a", "text/plain")
    assert mime.extract_part(HAND_MADE["cte-b64-midpad"], 1) == (b"hihi", "text/plain")


def test_where_python_failed_the_core_answers():
    """Python raised on each of these, so the message couldn't be read at all: a Date in a year past what a datetime
    holds, a Cc that is one quote mark, a Content-Transfer-Encoding inside thousands of open comments."""
    huge = headers(b"Date: Mon, 2 Nov 99999999999999999999 12:00:00 +0000")
    with pytest.raises(OverflowError):
        python_parse(huge)
    assert mime.parse(huge)["date"] == "Mon, 2 Nov 99999999999999999999 12:00:00 +0000"

    quote = headers(b'Cc: "')
    with pytest.raises(IndexError):
        python_parse(quote)
    assert mime.parse(quote)["cc"] == '"'

    comments = HAND_MADE["cte-att-nested-comments"]
    with pytest.raises(RecursionError):
        python_extract_part(comments, 2)
    assert mime.extract_part(comments, 2) == (b"SGk=", "application/octet-stream")   # all comment: no encoding


def test_an_8bit_reply_to_reads_as_utf8():
    """Python read a raw 8-bit byte in Reply-To as a lone surrogate, which no UTF-8 column can store."""
    raw = headers(b"Reply-To: d\xe9sk@example.test")
    assert python_parse(raw)["reply_to"] == "d\udce9sk@example.test"
    assert mime.parse(raw)["reply_to"] == "d�sk@example.test"


def test_an_attached_email_comes_as_it_was_sent():
    """Python wrote an attached email out again from what it had parsed of it, with LF line ends, kept a delivery
    report's first block alone, and handed over a base64-encoded email still encoded. The core hands over the bytes
    as they were sent, decoded, and their size is theirs: a download opens as the email that was attached."""
    plain = HAND_MADE["rfc822-plain"]
    assert python_extract_part(plain, 2)[0] == INNER.replace(b"\r\n", b"\n")
    assert mime.extract_part(plain, 2) == (INNER, "message/rfc822")
    assert mime.parse(plain)["attachments"][0]["size"] == len(INNER)
    assert mime.extract_part(HAND_MADE["rfc822-base64"], 2) == (INNER, "message/rfc822")
    assert (python_extract_part(HAND_MADE["rfc822-base64"], 2)[0] or b"").startswith(b"\nRnJvbTog")
    assert mime.extract_part(HAND_MADE["rfc822-empty"], 2) == (b"", "message/rfc822")   # Python: b"\n"

    report = HAND_MADE["delivery-status"]
    assert python_extract_part(report, 2)[0] == b"Reporting-MTA: dns; mx.example.test\n\n"
    assert mime.extract_part(report, 2) == (b"Reporting-MTA: dns; mx.example.test\r\n\r\nFinal-Recipient: rfc822; "
                                            b"x@example.test\r\nStatus: 5.1.1", "message/delivery-status")
    assert mime.extract_part(report, 3) == (b"From: a@example.test\r\nSubject: x", "message/rfc822-headers")
    assert mime.extract_part(HAND_MADE["digest"], 1) == (b"From: a@example.test\r\nSubject: one\r\n\r\nfirst", "message/rfc822")


def test_a_line_that_holds_a_delimiter_delimits():
    """RFC 2046 has a boundary appear nowhere in a multipart but in its delimiters, so a line that holds one is one,
    whatever else is on it. Python read such a multipart as no parts and showed its MIME source as the text; and
    where an inner multipart reused its outer one's boundary, Python lost the HTML after it."""
    for name in ("boundary-midline", "boundary-trailing-garbage"):
        assert (python_parse(HAND_MADE[name])["text"] or "").endswith("Content-Type: text/plain\r\n\r\nhi\r\n")
        assert mime.parse(HAND_MADE[name])["text"] == "hi"
        assert mime.extract_part(HAND_MADE[name], 1) == (b"hi", "text/plain")
    nested = HAND_MADE["same-boundary-nested"]
    assert (python_parse(nested)["text"], python_parse(nested)["html"]) == ("", None)
    assert (mime.parse(nested)["text"], mime.parse(nested)["html"]) == ("plain", "<p>h</p>")


def test_lines_that_end_in_cr_alone_end_in_crlf():
    """Old Macs ended lines in CR alone, which nothing shows as a line break now."""
    assert python_parse(HAND_MADE["cr-only"])["text"] == "line one\rline two\r"
    assert mime.parse(HAND_MADE["cr-only"])["text"] == "line one\r\nline two\r\n"


def test_a_file_name_reads_as_written():
    """An empty filename falls back to the type's name, and an unquoted encoded word decodes, where Python made up
    `part-2`. A broken encoded word shows as written, as RFC 2047 allows, where Python showed what it guessed of it,
    a name no one wrote."""
    def name(case: str, read=mime.parse) -> str:
        return read(HAND_MADE[case])["attachments"][0]["filename"]
    assert (name("fn-empty-filename"), name("fn-empty-filename", python_parse)) == ("a.pdf", "part-2")
    assert (name("fn-ew-unquoted"), name("fn-ew-unquoted", python_parse)) == ("résumé.pdf", "part-2")
    assert (name("fn-ew-broken-b64"), name("fn-ew-broken-b64", python_parse)) == ("=?utf-8?B?cmVzdW1*?=.pdf", "resum.pdf")


def test_a_type_with_a_comment_is_that_type():
    """`text/html (comment)` is text/html, RFC 2045 says. Python kept the comment in the type, so the HTML didn't show."""
    raw = HAND_MADE["part-ctype-comment"]
    assert python_extract_part(raw, 1) == (b"<b>c</b>", "text/html (comment)")
    assert mime.extract_part(raw, 1) == (b"<b>c</b>", "text/html")
    assert mime.parse(raw)["html"] == "<b>c</b>"


def test_an_address_shows_what_was_written():
    """A comment after an address was the old way to name its owner, and shows as the name, where Python dropped it.
    The blanks between two encoded words go, as RFC 2047 says. A broken encoded word, or an address cut short, shows
    as written, where Python showed a guess or `<>`."""
    def to(case: str, read=mime.parse) -> str:
        return read(HAND_MADE[case])["to"]
    assert (to("to-03"), to("to-03", python_parse)) == ("Bo Lin <bo@example.test>", "bo@example.test")
    assert (to("to-02"), to("to-02", python_parse)) == ("comment c2 <y@example.test>", "y@example.test")
    assert (to("to-38"), to("to-38", python_parse)) == ('"Bo (Lin)" <bo@example.test>', "Bo <bo@example.test>")
    assert (to("to-12"), to("to-12", python_parse)) == ("Bo Lin <bo@example.test>", "Bo  Lin <bo@example.test>")
    assert (to("to-14"), to("to-14", python_parse)) == ("=?utf-8?B?Qm8*?= <bo@example.test>", "Bo <bo@example.test>")
    assert (to("to-33"), to("to-33", python_parse)) == ("@example.test", "<>")
    assert (to("to-34"), to("to-34", python_parse)) == ("bo@", "<>")

