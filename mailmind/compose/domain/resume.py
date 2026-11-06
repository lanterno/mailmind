"""A saved draft, opened back up: what you typed, the quote it carries and its files."""
import base64
import mimetypes
import re

from mailmind.compose.domain import limits
from mailmind.compose.domain.errors import Refused

MODES = ("new", "reply", "replyall", "forward")
PARTICIPANT = re.compile(r'[^\s<>,;"]+@[^\s<>,;"]+')


def lf(s):
    """Text with LF line ends only."""
    return (s or "").replace("\r\n", "\n").replace("\r", "\n")


def resume_split(text, rec, exact):
    """Split a saved draft's text/plain into (own, tail, how), or None when that can't be done
    without losing something. rec: {"own_text", "tail_text"} as recorded at MailMind's last
    save of this draft, or None. exact: this is the very copy MailMind saved, so the record is
    the truth even when the server rewrote the bytes. how is "exact" (the record), "tail" (the
    text still ends with the recorded quote, so only that is cut) or "whole" (none of it can be
    told apart, so all of it is yours to edit). own + tail is the drafted text, give or take
    trailing newlines."""
    rec = rec or {}
    if exact and rec.get("own_text") is not None:
        return lf(rec["own_text"]), lf(rec.get("tail_text") or ""), "exact"
    if text is None:
        return None
    t, tail = lf(text), lf(rec.get("tail_text") or "")
    s, b = tail.rstrip("\n"), t.rstrip("\n")
    if s.startswith("\n\n") and b.endswith(s):
        return b[:len(b) - len(s)], tail, "tail"
    return (t[:-1] if t.endswith("\n") else t), "", "whole"   # set_content's own trailing newline


def resume_files(msg, parsed, elsewhere):
    """A draft's files as the composer takes them, [{filename, content_type, size, b64}], and how
    many were pictures in the text: every part must come back as the text or a file, or this
    raises Refused(422). Headers parse as they're read, so a hostile one raises here as well."""
    lost = "Part of this draft is something MailMind can’t reopen without losing it." + elsewhere
    if any(p.get_content_maintype() == "message" for p in msg.walk() if p is not msg):
        raise Refused(422, "This draft has an email attached as a file, which MailMind can’t "
                         "reattach yet." + elsewhere)
    if any(p.get_content_maintype() == "multipart" and not p.is_multipart() and str(p.get_payload() or "").strip()
           for p in msg.walk()):                         # data in a "container" with no boundary: no reader lists it
        raise Refused(422, lost)
    leaves = [p for p in msg.walk() if p.get_content_maintype() != "multipart"]   # parse()'s part_id order
    listed = {int(a["part_id"]) for a in parsed["attachments"]}
    body = next((p for i, p in enumerate(leaves, 1) if i not in listed and p.get_content_type() == "text/plain"), None)
    covered = set()                                      # HTML that the text parse() read is the alternative to
    for p in msg.walk():
        if p.get_content_type() == "multipart/alternative" and any(x is body for x in p.iter_parts()):
            covered |= {id(x) for x in p.walk() if x.get_content_type() == "text/html"}
    bodies = set()
    for i, p in enumerate(leaves, 1):                    # every part must come back as text or a file
        ct = p.get_content_type()
        if ct == "text/calendar" and not p.get_filename() and p.get_content_disposition() != "attachment":
            raise Refused(422, "This draft is a calendar invite, which MailMind can’t send on as "
                             "one." + elsewhere)
        if i in listed or p is body or id(p) in covered:
            continue                                     # a file, the text, or formatting of that same text
        if ct == "text/html" and body is None and ct not in bodies:
            bodies.add(ct)                               # formatted text only: refused below as such
            continue
        if not (p.get_payload(decode=True) or b"").strip():
            continue                                     # nothing in it to lose
        raise Refused(422, lost)
    files, total, inline = [], 0, 0
    for a in parsed["attachments"]:
        part = leaves[int(a["part_id"]) - 1]
        data = part.get_payload(decode=True) or b""
        ct = (part.get_content_type() or "application/octet-stream").lower()
        total += len(data)
        if total > limits.MAX_ATTACH_BYTES:
            raise Refused(422, "This draft’s files add up to more than 25 MB, more than MailMind "
                             "can send." + elsewhere)
        name = a["filename"]
        if a["content_id"] and a["is_inline"]:
            inline += 1                                  # a picture in the text: it comes back as a file
            if re.fullmatch(r"inline-\d+", name):
                name += mimetypes.guess_extension(ct or "") or ""
        files.append({"filename": name, "content_type": ct,
                      "size": len(data), "b64": base64.b64encode(data).decode("ascii")})
    return files, inline
