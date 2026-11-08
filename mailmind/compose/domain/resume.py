"""A saved draft, opened back up: which copy may be believed, what you typed, the quote it carries and its files."""
import base64
from dataclasses import dataclass
import email
import email.policy
from email.message import EmailMessage
import hashlib
import json
import mimetypes
import re
from typing import Literal, TypedDict, get_args

from mailmind.compose.domain import limits
from mailmind.kernel.errors import Unreadable
from mailmind.content.headers import header_chips, header_text
from mailmind.content.mime import Parsed, decoded, parse
from mailmind.kernel import addresses
from mailmind.kernel.row import Row

type ComposeMode = Literal["new", "reply", "replyall", "forward"]     # what a composer is: a request's schema names it so
MODES: tuple[ComposeMode, ...] = get_args(ComposeMode.__value__)
REPLIES = ("reply", "replyall")
PARTICIPANT = re.compile(r'[^\s<>,;"]+@[^\s<>,;"]+')
ELSEWHERE = " Edit it in the app that made it."
UNREADABLE = "MailMind can’t read this draft." + ELSEWHERE


class Record(TypedDict, total=False):
    """What MailMind's last save of a draft recorded the composer held."""
    own_text: str | None
    tail_text: str | None


class ResumedFile(TypedDict):
    """A file as the composer takes it back."""
    filename: str
    content_type: str
    size: int
    b64: str


@dataclass(frozen=True)
class Trust:
    """What a draft's record may be believed of, for the copy being reopened."""
    same_copy: bool             # the very copy the row tracks
    exact: bool                 # that, or the very bytes it recorded: only then does what it says hold for this copy


def lf(s: str | None) -> str:
    """Text with LF line ends only."""
    return (s or "").replace("\r\n", "\n").replace("\r", "\n")


def sha_of(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def trust(d: Row, row: Row, uidvalidity: int | None, raw: bytes) -> Trust:
    """`d` is the draft's row, `row` the copy opened with `raw` its bytes, and `uidvalidity` its folder's epoch now. The same
    copy is the same folder and UID, in the same epoch where both are known. A server may rewrite the bytes of a copy it
    keeps, and another app may give the same bytes a new UID, so either vouches."""
    same = (d["uid"] is not None and d["uid"] == row["uid"] and d["folder"] == row["folder"]
            and (d["uidvalidity"] is None or uidvalidity is None or d["uidvalidity"] == uidvalidity))
    return Trust(same, bool(same or (d["raw_sha"] and d["raw_sha"] == sha_of(raw))))


def copy_moved(before: Row | None, now: Row | None) -> bool:
    """Did the draft's row move while this waited for the gate? A save or a send that held it may have replaced or removed
    the copy the row tracked."""
    return before is not None and (now is None or (now["uid"], now["message_id"]) != (before["uid"], before["message_id"]))


def copy_is_there(uidvalidity: int | None, known: int | None, there: bool) -> bool:
    """What a probe of a copy says, given the epoch it saw and the one held: every UID held for a folder renumbered is void."""
    return False if uidvalidity is not None and known is not None and uidvalidity != known else there


def read(raw: bytes) -> tuple[EmailMessage, Parsed]:
    """A draft's bytes, for what follows here. Raises on bytes that won't parse."""
    return email.message_from_bytes(raw, policy=email.policy.default), parse(raw)


def resume_split(text: str | None, rec: Record | None, exact: bool) -> tuple[str, str, str] | None:
    """Split a saved draft's text/plain into (own, tail, how), or None when that can't be done
    without losing something. rec: {"own_text", "tail_text"} as recorded at MailMind's last
    save of this draft, or None. exact: this is the very copy MailMind saved, so the record is
    the truth even when the server rewrote the bytes. how is "exact" (the record), "tail" (the
    text still ends with the recorded quote, so only that is cut) or "whole" (none of it can be
    told apart, so all of it is yours to edit). own + tail is the drafted text, give or take
    trailing newlines."""
    rec = rec or {}
    own = rec.get("own_text")
    if exact and own is not None:
        return lf(own), lf(rec.get("tail_text") or ""), "exact"
    if text is None:
        return None
    t, tail = lf(text), lf(rec.get("tail_text") or "")
    s, b = tail.rstrip("\n"), t.rstrip("\n")
    if s.startswith("\n\n") and b.endswith(s):
        return b[:len(b) - len(s)], tail, "tail"
    return (t[:-1] if t.endswith("\n") else t), "", "whole"   # set_content's own trailing newline


def split_to_fit(text: str | None, record: Row, exact: bool, files: list[ResumedFile]) -> tuple[str, str, str]:
    """resume_split's answer, if the draft can come back at all. Unreadable with formatted text only, whose links and
    formatting would be lost, and past what a save can carry: every save and send of it carries the files as base64 and the
    text and quote as JSON, so past limits.MAX_SEND_POST each would be refused, and edits made in the composer lost on
    close."""
    split = resume_split(text, {"own_text": record["own_text"], "tail_text": record["tail_text"]}, exact)
    if split is None:
        raise Unreadable("This draft only has formatted text. Edit it in the app that made it, "
                         "so its links and formatting aren’t lost.")
    size = sum(len(f["b64"]) for f in files) + sum(len(json.dumps(t, ensure_ascii=False).encode()) for t in split[:2])
    if size + limits.RESUME_HEADROOM > limits.MAX_SEND_POST:
        raise Unreadable("This draft is too large for MailMind to save or send, with its files "
                         "and the text it quotes." + ELSEWHERE)
    return split


def mode_of(stored: str | None, in_reply_to: str | None) -> ComposeMode:
    """How a draft reopens. MailMind's own new drafts never carry an In-Reply-To, so one that does was written as a reply."""
    mode = stored if stored in MODES else "new"
    return "reply" if mode == "new" and in_reply_to else mode


def participants_of(original: Row) -> list[str]:
    """Who a reply-all drafted from this original could reach: each once, lowercase, as far as the stored row names them."""
    out: list[str] = []
    for a in [original["from_addr"] or ""] + PARTICIPANT.findall(original["recipients"] or ""):
        a = a.strip().lower()
        if a and a not in out:
            out.append(a)
    return out


def _address_of(chip: str) -> str:
    try:
        return addresses.parse_addr_list([chip])[0][1].lower()
    except (ValueError, IndexError):
        return ""


def bcc_of(msg: EmailMessage, recorded: str | None, exact: bool) -> tuple[list[str], int]:
    """(the draft's Bcc as chips, how many were dropped). Another app may keep Bcc in the draft itself. What MailMind recorded
    joins it only where the record is this copy's, and is otherwise counted, so the composer can say some went."""
    hdr = header_chips(msg, "Bcc")
    had = {_address_of(x) for x in hdr}
    kept: list[str] = json.loads(recorded) if recorded else []
    missing = [x for x in kept if _address_of(x) and _address_of(x) not in had]
    return (hdr + missing, 0) if exact else (hdr, len(missing))


def subject_of(msg: EmailMessage, then: str | None) -> str:
    """As drafted: the synced row's copy is unfolded with spaces. Failing that, what the row said."""
    subject = header_text(msg, "Subject")
    return subject if subject is not None else (then or "")


def resume_files(msg: EmailMessage, parsed: Parsed) -> tuple[list[ResumedFile], int]:
    """A draft's files as the composer takes them, [{filename, content_type, size, b64}], and how
    many were pictures in the text: every part must come back as the text or a file, or this
    raises Unreadable. Headers parse as they're read, so a hostile one raises here as well."""
    lost = "Part of this draft is something MailMind can’t reopen without losing it." + ELSEWHERE
    if any(p.get_content_maintype() == "message" for p in msg.walk() if p is not msg):
        raise Unreadable("This draft has an email attached as a file, which MailMind can’t "
                         "reattach yet." + ELSEWHERE)
    if any(p.get_content_maintype() == "multipart" and not p.is_multipart() and str(p.get_payload() or "").strip()
           for p in msg.walk()):                         # data in a "container" with no boundary: no reader lists it
        raise Unreadable(lost)
    leaves = [p for p in msg.walk() if p.get_content_maintype() != "multipart"]   # parse()'s part_id order
    listed = {int(a["part_id"]) for a in parsed["attachments"]}
    body = next((p for i, p in enumerate(leaves, 1) if i not in listed and p.get_content_type() == "text/plain"), None)
    covered: set[int] = set()                            # HTML that the text parse() read is the alternative to
    for p in msg.walk():
        if p.get_content_type() == "multipart/alternative" and any(x is body for x in p.iter_parts()):
            covered |= {id(x) for x in p.walk() if x.get_content_type() == "text/html"}
    bodies: set[str] = set()
    for i, p in enumerate(leaves, 1):                    # every part must come back as text or a file
        ct = p.get_content_type()
        if ct == "text/calendar" and not p.get_filename() and p.get_content_disposition() != "attachment":
            raise Unreadable("This draft is a calendar invite, which MailMind can’t send on as "
                             "one." + ELSEWHERE)
        if i in listed or p is body or id(p) in covered:
            continue                                     # a file, the text, or formatting of that same text
        if ct == "text/html" and body is None and ct not in bodies:
            bodies.add(ct)                               # formatted text only: refused below as such
            continue
        if not (decoded(p) or b"").strip():
            continue                                     # nothing in it to lose
        raise Unreadable(lost)
    files: list[ResumedFile] = []
    total = inline = 0
    for a in parsed["attachments"]:
        part = leaves[int(a["part_id"]) - 1]
        data = decoded(part) or b""
        ct = (part.get_content_type() or "application/octet-stream").lower()
        total += len(data)
        if total > limits.MAX_ATTACH_BYTES:
            raise Unreadable("This draft’s files add up to more than 25 MB, more than MailMind "
                             "can send." + ELSEWHERE)
        name = a["filename"]
        if a["content_id"] and a["is_inline"]:
            inline += 1                                  # a picture in the text: it comes back as a file
            if re.fullmatch(r"inline-\d+", name):
                name += mimetypes.guess_extension(ct or "") or ""
        files.append({"filename": name, "content_type": ct,
                      "size": len(data), "b64": base64.b64encode(data).decode("ascii")})
    return files, inline
