"""Which folder plays which part on an account's server.

A folder's role is one of ROLES, or "custom". It comes, in order, from:
1. Its name, when that is INBOX, which IMAP reserves.
2. Its SPECIAL-USE attribute (RFC 6154): \\Sent, \\Drafts, \\Archive, \\Junk, \\Trash or \\All.
3. Its name: the last segment of its path, the whole segment, matched against the names servers
   and mail clients give each role ("Sent Items", "Junk E-mail", "Deleted Messages"...). Part of
   a name never counts, so "Bulk orders" is nobody's junk.
Where several folders could play one role, an attribute beats a name, a shallower path a deeper
one ("Archive" over Proton Bridge's "Folders/Archive"), and then the name, in code point order.
Never the server's order: Proton Bridge lists its folders in a new order each time.

`all` is a folder holding every message, Gmail's All Mail and Proton's alike. Archiving moves to
`archive`, and only where there is none to `all`.
"""
import re
from dataclasses import dataclass

ROLES = ("inbox", "sent", "drafts", "archive", "all", "junk", "trash")

_ATTRIBUTES = {"\\sent": "sent", "\\drafts": "drafts", "\\archive": "archive", "\\all": "all",
               "\\junk": "junk", "\\trash": "trash"}

_SUFFIX = r"(?:[ _-]?(?:mail|e-?mail|messages?|items?|folder))?"
_NAMES = [(role, re.compile(rx)) for role, rx in (
    ("sent", r"sent" + _SUFFIX),
    ("drafts", r"drafts?"),
    ("archive", r"archives?"),
    ("all", r"all[ _-]?mail"),
    ("junk", r"(?:junk|spam|bulk)" + _SUFFIX),
    ("trash", r"(?:trash|bin|deleted)" + _SUFFIX),
)]


@dataclass(frozen=True)
class Folder:
    """A folder as LIST gave it: its name in LIST's form (accounts.mailbox_arg sends it back), its
    attributes in lower case ("\\noselect", "\\junk"...), and the server's hierarchy delimiter,
    None when LIST said NIL."""
    name: str
    attributes: frozenset = frozenset()
    delimiter: str | None = "/"

    @property
    def selectable(self):
        return not (self.attributes & {"\\noselect", "\\nonexistent"})

    @property
    def depth(self):
        return self.name.count(self.delimiter) if self.delimiter else 0


def _last_segment(name, delimiter):
    name = name.replace('\\"', '"').replace("\\\\", "\\")
    parts = name.split(delimiter) if delimiter else re.split(r"[/.]", name)
    return parts[-1].strip().lower()


def role_by_name(name, delimiter=None):
    """The role a folder's name alone gives it. With no delimiter, both "/" and "." separate the
    path, as a stored folder name carries none."""
    if (name or "").upper() == "INBOX":
        return "inbox"
    seg = _last_segment(name or "", delimiter)
    for role, rx in _NAMES:
        if rx.fullmatch(seg):
            return role
    return "custom"


def _by_attribute(folder):
    for a in folder.attributes:
        if a in _ATTRIBUTES:
            return _ATTRIBUTES[a]
    return None


def role_of(folder):
    """The role `folder` plays: INBOX by name, then its attribute, then its name."""
    if folder.name.upper() == "INBOX":
        return "inbox"
    return _by_attribute(folder) or role_by_name(folder.name, folder.delimiter)


def find(folders, *roles):
    """The name of the selectable folder playing the first of `roles` that one plays, or None."""
    for role in roles:
        found = [(0 if role == "inbox" or _by_attribute(f) == role else 1, f.depth, f.name)
                 for f in folders if f.selectable and role_of(f) == role]
        if found:
            return min(found)[2]
    return None
