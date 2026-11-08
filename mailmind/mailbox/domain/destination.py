"""Where a filing verb sends a message on an account's server. Gmail's archive goes nowhere: the message stays in All
Mail, and only its \\Inbox label goes. `find` asks the server which folder plays a role, and only a verb that needs to
know calls it."""
from dataclasses import dataclass
from typing import Protocol


class FindRole(Protocol):
    """Which folder plays the first of these roles that one plays, asked of the server."""
    def __call__(self, *roles: str) -> str | None: ...


@dataclass(frozen=True)
class Destination:
    folder: str | None = None           # None, with no label to drop: the account has nowhere to put it
    drop_inbox_label: bool = False


# The parts a folder may play and a person still not file mail into. A message in Drafts counts as a draft, which Discard and a
# save remove, one in Sent counts as sent, and Gmail's All Mail is where mail already is: archive is how it gets there alone.
CLOSED_TO_FILING = frozenset({"drafts", "sent", "all"})


def takes_filing(role: str) -> bool:
    """Whether a person may move mail into a folder that plays `role`."""
    return role not in CLOSED_TO_FILING


def resolve(kind: str, *, named: str | None, archive_is_label_removal: bool, trash: str | None,
            junk_fallback: str | None, find: FindRole) -> Destination:
    """The Destination of a `kind` of filing. `named` is where the command's row already says it goes: a folder a person
    picked, for a `move`."""
    if kind == "archive" and archive_is_label_removal:
        return Destination(drop_inbox_label=True)
    if named:
        return Destination(named)
    if kind == "trash":
        return Destination(trash)
    if kind == "junk":
        return Destination(find("junk") or junk_fallback)
    if kind == "archive":
        return Destination(find("archive", "all"))
    return Destination()


def _no_server(*_roles: str) -> str | None:
    return None


def known_without_server(kind: str, *, archive_is_label_removal: bool, trash: str | None) -> Destination:
    """As much of `resolve` as holds with no server to ask, as a command is accepted and its Undo written. A folder only
    the server can name is left for a try to find, so no folder here doesn't yet mean nowhere to go."""
    return resolve(kind, named=None, archive_is_label_removal=archive_is_label_removal, trash=trash, junk_fallback=None,
                   find=_no_server)
