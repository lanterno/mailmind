"""Where a filing verb sends a message on an account's server. Gmail's archive goes nowhere: the message stays in All
Mail, and only its \\Inbox label goes. `find` asks the server which folder plays a role, and only a verb that needs to
know calls it."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Destination:
    folder: str | None = None           # None, with no label to drop: the account has nowhere to put it
    drop_inbox_label: bool = False


def resolve(kind, *, named, archive_is_label_removal, trash, junk_fallback, find):
    """The Destination of a `kind` of filing. `named` is where the command already says it goes."""
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
