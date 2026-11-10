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
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import TypedDict

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
    attributes: frozenset[str] = frozenset()
    delimiter: str | None = "/"

    @property
    def selectable(self) -> bool:
        return not (self.attributes & {"\\noselect", "\\nonexistent"})

    @property
    def depth(self) -> int:
        """How deep the folder sits: by the server's delimiter, or with none, by "/" and "." as `role_by_name` reads
        the path, so of "Trash" and "Old/Trash" the shallower plays the part wherever the names came from."""
        if self.delimiter:
            return self.name.count(self.delimiter)
        return len(re.findall(r"[/.]", self.name))


def _last_segment(name: str, delimiter: str | None) -> str:
    name = name.replace('\\"', '"').replace("\\\\", "\\")
    parts = name.split(delimiter) if delimiter else re.split(r"[/.]", name)
    return parts[-1].strip().lower()


def role_by_name(name: str | None, delimiter: str | None = None) -> str:
    """The role a folder's name alone gives it. With no delimiter, both "/" and "." separate the
    path, as a stored folder name carries none."""
    if (name or "").upper() == "INBOX":
        return "inbox"
    seg = _last_segment(name or "", delimiter)
    for role, rx in _NAMES:
        if rx.fullmatch(seg):
            return role
    return "custom"


_DRAFTS_PARENTS = {"inbox", "[gmail]", "[google mail]"}


def names_drafts(name: str) -> bool:
    """Whether a folder's name alone says it is the account's Drafts, with no LIST to ask: at the
    top of the tree, or under the parents servers keep it in. "Clients/Draft" is a folder someone
    made, so what it holds may be anything."""
    if role_by_name(name) != "drafts":
        return False
    path = re.split(r"[/.]", name)
    return len(path) == 1 or (len(path) == 2 and path[0].strip().lower() in _DRAFTS_PARENTS)


def _by_attribute(folder: Folder) -> str | None:
    for a in folder.attributes:
        if a in _ATTRIBUTES:
            return _ATTRIBUTES[a]
    return None


def role_of(folder: Folder) -> str:
    """The role `folder` plays: INBOX by name, then its attribute, then its name."""
    if folder.name.upper() == "INBOX":
        return "inbox"
    return _by_attribute(folder) or role_by_name(folder.name, folder.delimiter)


def find(folders: Collection[Folder], *roles: str) -> str | None:
    """The name of the selectable folder playing the first of `roles` that one plays, or None."""
    for role in roles:
        found = [(0 if role == "inbox" or _by_attribute(f) == role else 1, f.depth, f.name)
                 for f in folders if f.selectable and role_of(f) == role]
        if found:
            return min(found)[2]
    return None


class RailFolder(TypedDict):
    name: str
    role: str
    unseen: int
    total: int


_RAIL_PLACE = {"inbox": 0, "sent": 1, "drafts": 2, "archive": 3, "all": 4, "junk": 8, "trash": 9}
_ANY_OTHER = 5                  # between All Mail and Junk


def rail_order(folders: Iterable[RailFolder]) -> list[RailFolder]:
    """The rail's order: the folders that play a part, in theirs, with a person's own between them, by name."""
    return sorted(folders, key=lambda f: (_RAIL_PLACE.get(f["role"], _ANY_OTHER), f["name"].lower()))


def playing(folders: Collection[Folder]) -> dict[str | None, str]:
    """{name: role} of the folders `find` gives a role: one folder a role, so a second with that name is `custom`."""
    return {find(folders, r): r for r in ROLES}


def stored_rail(counts: Mapping[str, tuple[int | None, int]]) -> list[RailFolder]:
    """The rail from what is stored alone, with no server to ask, from {folder: (unseen, total)}. INBOX is always on it. Roles
    go by name, as a live list's do: a stored name carries no delimiter, so "/" and "." both mark its depth, and of two
    that could play one the shallower does, then the lower name."""
    held = dict(counts)
    held.setdefault("INBOX", (0, 0))
    names = sorted(held, key=lambda n: (len(re.findall(r"[/.]", n)), n.lower()))
    plays = playing([Folder(n, frozenset(), None) for n in names])
    return rail_order([{"name": n, "role": plays.get(n, "custom"), "unseen": held[n][0] or 0, "total": held[n][1] or 0} for n in names])
