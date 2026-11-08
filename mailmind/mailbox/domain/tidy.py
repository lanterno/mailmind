"""What a Tidy that runs by itself may file, with no I/O: what the brief's Tidy offers (`attention.safe_to_file`), of what is
still in an Inbox, and never what a person has already taken back."""
from collections.abc import Collection

from mailmind.kernel.attention import safe_to_file
from mailmind.kernel.ids import INBOX
from mailmind.kernel.row import Row

FIRST_HOUR = 6              # local: not before someone's day has started
READS = 400                 # as many as the brief's list holds
PREFIX = "tidy-"            # a run's undo group and its commands' mutation id: tidy-<local day>


def group_of(day: str) -> str:
    return PREFIX + day


def is_run(group: object) -> bool:
    return isinstance(group, str) and group.startswith(PREFIX)


def takes(row: Row, taken_back: Collection[tuple[str, str]]) -> bool:
    """Whether a digest row is one a run may file. `taken_back` holds (account, Message-ID) of each archive an Undo reversed."""
    return (row["folder"] == INBOX and row["message_key"] is not None and row["uid"] is not None
            and (row["account"], row["message_id"]) not in taken_back
            and safe_to_file(row["importance"], row["category"], row["action"]))
