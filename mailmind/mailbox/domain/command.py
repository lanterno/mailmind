"""A change to the mail on a server, as it goes through the one path: accepted, tried, settled."""
from dataclasses import dataclass

from mailmind.kernel.row import Row

APPLIED = "applied"             # the server holds the change, or the message had already left its folder
QUEUED = "queued"               # the server can't be reached, or this process only reads: the worker has it
WAITING = "waiting"             # the account's gate didn't come free in time: the worker has it
REFUSED = "refused"             # the server said NO or BAD: nothing happened, and no retry will make it
NO_FOLDER = "no_folder"         # the account has no folder to move it to
REMOVED = "removed"             # the account is gone
CANCELLED = "cancelled"         # an Undo or a Discard got there first

WONT_HAPPEN = (REFUSED, NO_FOLDER, REMOVED)


@dataclass(frozen=True)
class Accepted:
    """A command's row, written in one transaction with what it hides and what reverses it."""
    id: int | None               # None only for a request seen before whose row has since gone
    account: str
    created: bool               # False: this request was seen before, and nothing new was written
    undoable: bool
    claimed: Row | None = None  # its row, claimed for a try now; None when only the worker may try it


@dataclass(frozen=True)
class ServerAnswer:
    """What a try at a command left on the server."""
    won_t: str | None = None    # one of WONT_HAPPEN; None once the server holds the change
    dest: str | None = None     # the folder it went to
    unseen: bool = False        # the server marked it read on the way to Trash: a reversal takes that off
