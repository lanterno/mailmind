"""Folder names and other strings as IMAP command arguments.

A folder name keeps the form LIST gave it: the inside of a quoted string, with any \\" or \\\\
escapes in place, as the database stores it. mailbox_arg only wraps it, and refuses one that
isn't a valid quoted-string body. unescaped turns it back into the name itself, for a client
that quotes on its own (IMAPClient).

Standard library only.
"""
import imaplib
import re


class BadMailbox(imaplib.IMAP4.error):
    """A folder name that can't be sent as an IMAP quoted string. It is refused before it reaches
    the wire and handled like the server's own BAD: a failed command, never retried."""


_MAILBOX_BODY = re.compile(r'(?:[^"\\\r\n\x00]|\\["\\])*')


def imap_quote(s):
    """Wrap a value as an IMAP quoted-string so embedded quotes/specials (e.g. a malformed
    sender Message-ID like <…b'AB'…>) can't break a SEARCH/command."""
    return '"' + (s or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


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
