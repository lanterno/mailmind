"""A change to the mail on a server, as it goes through the one path: accepted, tried, settled. What its row says, what
reverses it and what the tray shows of it are here too."""
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypeGuard

from mailmind.kernel.row import Row
from mailmind.mailbox.domain.destination import Destination

APPLIED = "applied"             # the server holds the change, or the message had already left its folder
QUEUED = "queued"               # the server can't be reached, or this process only reads: the worker has it
WAITING = "waiting"             # the account's gate didn't come free in time: the worker has it
REFUSED = "refused"             # the server said NO or BAD: nothing happened, and no retry will make it
NO_FOLDER = "no_folder"         # the account has no folder to move it to
REMOVED = "removed"             # the account is gone
CANCELLED = "cancelled"         # an Undo or a Discard got there first

WONT_HAPPEN = (REFUSED, NO_FOLDER, REMOVED)

_UNDO_LABEL = {"trash": "Trashed", "archive": "Archived", "junk": "Marked junk", "move": "Moved"}
_REQUEST_ID_MAX = 128


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


def names_a_request(mutation_id: object) -> TypeGuard[str]:
    """Whether a client's `mutation_id` can name its request, so a resubmit of it dedupes. Without one every request is its
    own: a key built from the message alone made a second trash of a message moved back answer "queued" and do nothing."""
    return isinstance(mutation_id, str) and 0 < len(mutation_id) <= _REQUEST_ID_MAX


def idempotency_key(kind: str, account: str, message_id: str, pk: int, request: object) -> str:
    """What makes this request's row its own: the same request again finds it, and nothing else does."""
    return f"{kind}:{account}:{message_id or pk}:{request}"


def takes_a_move(row: Row | None, to: str | None) -> TypeGuard[Row]:
    """Whether a message can be asked to move: it is there, not already on its way out, and not already where `to` names."""
    if not row:
        return False
    return row["pending_removal"] is None and row["folder"] != to


def move_params(pk: int, row: Row, epoch: int | None) -> dict[str, Any]:
    """What a move's row keeps of its message as it is accepted, `epoch` being the folder's UIDVALIDITY then."""
    return {"pk": pk, "uid": row["uid"], "epoch": epoch, "seen": bool(row["seen"]), "subject": row["subject"],
            "thread_id": row["thread_id"]}


def undo_entry(kind: str, command_id: int, row: Row, message_id: str, where: Destination) -> dict[str, Any]:
    """What Undo keeps to reverse a move, and to show it."""
    return {"kind": "gmail_archive" if where.drop_inbox_label else "move", "command": command_id, "account": row["account"],
            "src_folder": row["folder"], "dest_folder": where.folder or "", "message_id": message_id,
            "label": _UNDO_LABEL.get(kind, "Updated"), "subject": row["subject"], "who": row["from_name"] or row["from_addr"],
            "thread_id": row["thread_id"]}


def reverses(entry: Mapping[str, Any], row: Row) -> bool:
    """Whether an undo `entry` is the one that reverses the move `row`."""
    return (entry.get("kind") in ("move", "gmail_archive") and entry.get("account") == row["account"]
            and entry.get("message_id") == row["message_id"] and entry.get("src_folder") == row["src_folder"])


def why_given_up(outcome: str) -> str:
    """What a row the worker gave up on says, for the tray."""
    return "account removed" if outcome == REMOVED else outcome


def tray_item(row: Row, params: Mapping[str, Any], subject: str) -> dict[str, Any]:
    """A change that hasn't reached a server, as the tray shows it."""
    return {"id": row["id"], "account": row["account"], "op": row["kind"], "folder": row["src_folder"], "dest": row["dest"],
            "flags": params.get("flags"), "state": row["state"], "attempts": row["attempts"], "error": row["last_error"],
            "subject": subject, "next_attempt_at": row["next_attempt_at"], "created_at": row["created_at"]}


def tray_counts(counts: Mapping[str, int]) -> dict[str, int]:
    """How many changes wait, a try included, and how many were given up on, from {state: how many}."""
    return {"waiting": counts.get("queued", 0) + counts.get("running", 0), "stuck": counts.get("error", 0)}
