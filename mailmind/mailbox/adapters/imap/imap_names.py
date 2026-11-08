"""Folder names and other strings as IMAP command arguments.

A folder name keeps the form LIST gave it: the inside of a quoted string, with any \\" or \\\\
escapes in place, as the database stores it. mailbox_arg only wraps it, and refuses one that
isn't a valid quoted-string body. unescaped turns it back into the name itself, for a client
that quotes on its own (IMAPClient).

A Gmail label comes off a FETCH response line in the same quoted form, and is read here as a person reads it.

Standard library only.
"""
import base64
import imaplib
import re


class BadMailbox(imaplib.IMAP4.error):
    """A folder name that can't be sent as an IMAP quoted string. It is refused before it reaches
    the wire and handled like the server's own BAD: a failed command, never retried."""


class BadValue(imaplib.IMAP4.error):
    """A value no IMAP quoted string can carry: a CR or LF would end the command line and start
    another, and a NUL ends nothing well. Refused before it reaches the wire, like BadMailbox. A
    sender can fold a Message-ID header so the stored ID holds a line break."""


_MAILBOX_BODY = re.compile(r'(?:[^"\\\r\n\x00]|\\["\\])*')
_UNSENDABLE = re.compile(r"[\r\n\x00]")
_SYSTEM_LABEL = re.compile(r"\\[A-Za-z]+")


def sendable(value):
    """`value` itself, if a quoted string can carry it; else BadValue."""
    if _UNSENDABLE.search(value or ""):
        raise BadValue("a value holding a line break or NUL")
    return value


def imap_quote(s):
    """Wrap a value as an IMAP quoted-string so embedded quotes/specials (e.g. a malformed
    sender Message-ID like <…b'AB'…>) can't break a SEARCH/command."""
    return '"' + sendable(s or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def gmail_labels(labels):
    """Gmail's labels as an X-GM-LABELS list: a system label (\\Inbox) as it is, any other quoted.
    Both adapters send this one form."""
    return "(" + " ".join(lb if _SYSTEM_LABEL.fullmatch(lb) else imap_quote(lb) for lb in labels) + ")"


_LABELS_AT = b"X-GM-LABELS ("
_LABEL = re.compile(rb'\s*(?:"((?:[^"\\]|\\.)*)"|([^\s()"]+))')
_LITERAL = re.compile(rb"\{\d+\}")
_SHIFTED = re.compile(r"&([^-]*)-")


def _shifted(found):
    """One run of IMAP's modified UTF-7: `&-` is an ampersand, and anything else base64 of UTF-16, with , for /."""
    if not found.group(1):
        return "&"
    try:
        run = found.group(1).replace(",", "/")
        return base64.b64decode(run + "=" * (-len(run) % 4)).decode("utf-16-be")
    except ValueError:
        return found.group(0)


def label_name(raw):
    """A Gmail label as a person reads it: IMAP names non-ASCII ones in modified UTF-7. A system label (\\Inbox) is as it came."""
    return _SHIFTED.sub(_shifted, raw)


def read_gmail_labels(line):
    """The labels on an IMAP FETCH response line: `X-GM-LABELS ("\\\\Inbox" Work "Two words")`, quoted or atoms. Stops at the
    closing paren. A literal (never seen from Gmail, and a line can't hold one) leaves the rest unread."""
    start = line.find(_LABELS_AT)
    if start < 0:
        return frozenset()
    at, out = start + len(_LABELS_AT), set()
    while True:
        one = _LABEL.match(line, at)
        if not one:
            break
        raw = one.group(1) if one.group(1) is not None else one.group(2)
        if one.group(2) is not None and _LITERAL.fullmatch(one.group(2)):
            break
        out.add(label_name(re.sub(rb'\\(["\\])', rb"\1", raw).decode("utf-8", "replace")))
        at = one.end()
    return frozenset(out)


def mailbox_arg(name):
    """A folder name as an IMAP command argument. Names keep the form LIST gave them, the inside of
    a quoted string with any \\" or \\\\ escapes in place, so this only wraps them. A name that
    isn't a valid quoted-string body is refused: a CR or LF would end the command line and start
    another command, and a bare quote or a trailing backslash would end the string early."""
    if not isinstance(name, str) or not name or not _MAILBOX_BODY.fullmatch(name):
        raise BadMailbox("bad folder name")
    return f'"{name}"'


def unescaped(name):
    """The folder `name` (in LIST's form) itself, checked as mailbox_arg checks it."""
    mailbox_arg(name)
    return re.sub(r'\\(["\\])', r"\1", name)


def escaped(raw):
    """A folder name as a client that unquotes LIST hands it back, in LIST's form."""
    return raw.replace("\\", "\\\\").replace('"', '\\"')
