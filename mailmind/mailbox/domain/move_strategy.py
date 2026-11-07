"""How a message moves on its account's server: the MoveStrategy. domain.destination says where it goes, and this says
which way, from the filing verb, what the account's profile says and what the server offers. It is a value, and nothing
here asks a server anything: ServerOps carries it out, and what a server answers on the way stays with whoever asked.

There are three ways:
- Way.LABELS: Gmail's archive goes nowhere. The message stays in All Mail, and only its \\Inbox label goes.
- Way.MOVE: a UID MOVE, on a server that offers one. MailSession.move goes by copy if the server refuses it.
- Way.COPY: on a server that doesn't (session.move_by_copy), a COPY, checked, then \\Deleted and a UID EXPUNGE of each UID
  alone.

Proton Bridge breaks on a bulk EXPUNGE. No way here sends one, on any server, so Bridge takes the same ways as any server
offering the same, and its profile's bulk_expunge_unsafe isn't asked. Bridge also marks a message read as it moves to
Trash, so a move there reads \\Seen first, which is how an Undo knows to take the mark off: the verb's, or a plain move
to the account's Trash folder.
"""
from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum

from mailmind.mailbox.domain.destination import Destination


class Way(Enum):
    LABELS = "labels"
    MOVE = "move"
    COPY = "copy"


@dataclass(frozen=True)
class MoveStrategy:
    way: Way
    read_seen_first: bool = False       # the server sets \Seen as it lands: a reversal needs to know what it was


def offers_move(capabilities: Collection[str]) -> bool:
    """Whether a server with these capabilities (upper-case names, as MailSession gives them) takes a UID MOVE."""
    return "MOVE" in capabilities


def choose(kind: str, where: Destination, *, capabilities: Collection[str], marks_trash_read: bool,
           trash: str | None = None) -> MoveStrategy:
    """Which way a `kind` of filing takes to `where`. Whether there is a folder to go to is `where`'s to say. `trash` is
    the account's Trash folder, where a server may set the read mark whichever verb sent the message."""
    if where.drop_inbox_label:
        return MoveStrategy(Way.LABELS)
    return MoveStrategy(Way.MOVE if offers_move(capabilities) else Way.COPY,
                        read_seen_first=marks_trash_read and (kind == "trash" or where.folder == trash))
