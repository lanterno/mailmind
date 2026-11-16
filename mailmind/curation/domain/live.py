"""INBOX mail as the server shows it right now, read for a sweep or for expiry. Mail is read live before anything moves,
so a stored row can't send the wrong message anywhere. `from` being a keyword, the base shapes are written as calls."""
import email
from collections.abc import Iterable
from typing import TypedDict

from mailmind.content import mime
from mailmind.content.mime import dec

READ_FIELDS = ("FROM", "SUBJECT", "MESSAGE-ID", "LIST-UNSUBSCRIBE", "LIST-ID")     # what a sweep asks the server for

LiveMail = TypedDict("LiveMail", {"uid": str, "from": str, "subject": str, "mid": str})
Audited = TypedDict("Audited", {"uid": str, "from": str, "subject": str, "bucket": str, "reason": str})


class Read(LiveMail):
    """With what a classifier wants from its headers, and whether a person starred it or hasn't read it yet."""
    name: str
    bulk: bool
    starred: bool
    unread: bool


def read_from_headers(uid: int, raw: bytes, flags: Iterable[str]) -> Read:
    """A message as a sweep reads it, from its READ_FIELDS and its flags. Either List header says it's bulk. A flag is
    compared whatever its case, as IMAP compares them, and a message no flag says was seen counts as unread."""
    msg = email.message_from_bytes(raw)
    name, addr = mime.sender_of(msg)
    held = {f.lower() for f in flags}
    return {"uid": str(uid), "from": addr.lower(), "name": dec(name),
            "subject": dec(msg.get("Subject", "")),
            "mid": (msg.get("Message-ID", "") or "").strip(),
            "bulk": bool(msg.get("List-Unsubscribe") or msg.get("List-ID")),
            "starred": "\\flagged" in held, "unread": "\\seen" not in held}


def held(r: Read) -> bool:
    """Whether a sweep leaves this message where it is: a person starred it, or hasn't read it yet."""
    return r["starred"] or r["unread"]


class Classified(Read):
    bucket: str
    conf: str
    reason: str


class Aged(LiveMail):
    """With when it arrived, in epoch seconds, if that's known."""
    ts: float | None


class Expired(Aged):
    """Past the shelf life of `hours` its sender's rule gives it."""
    hours: int
