"""A message's flags, in the three ways they are said: the server's words for them, the columns that hold them, and a change
someone asked for. And what a read of the server's flags may write: nothing over a change the app still owes the server, since
a refusal would need the flags that change replaced."""
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

from mailmind.kernel.row import Row

COLUMNS = ("seen", "flagged", "answered", "draft", "deleted")
IMAP_NAME = {"seen": "\\Seen", "flagged": "\\Flagged", "answered": "\\Answered", "draft": "\\Draft", "deleted": "\\Deleted"}


def columns_of(server_flags: Iterable[str]) -> dict[str, int]:
    """The local flag columns from a message's IMAP flags, whatever case the server wrote them in."""
    said = {f.lower() for f in server_flags}
    return {column: int(IMAP_NAME[column].lower() in said) for column in COLUMNS}


def has_seen(server_flags: Iterable[str]) -> bool:
    return "\\seen" in {f.lower() for f in server_flags}


def as_columns(change: Mapping[str, object]) -> dict[str, int]:
    """A change's flags as the columns hold them."""
    return {k: int(bool(v)) for k, v in change.items()}


def held_now(row: Row, flags: Collection[str]) -> dict[str, bool]:
    """What `row` holds of these flags: what a change to them replaces, for a refusal to put back."""
    return {k: bool(row[k]) for k in flags}


def still_to_say(wanted: Mapping[str, bool], row: Row) -> dict[str, bool]:
    """Of what is `wanted`, what `row` doesn't already hold: what it came with needs saying to no one."""
    return {flag: on for flag, on in wanted.items() if bool(row[flag]) != on}


@dataclass(frozen=True)
class FlagRead:
    """What a read of the server's flags comes to."""
    changed: list[tuple[Row, dict[str, int]]]   # each row the server says otherwise of, with its new columns
    held_back: bool                             # some row was left alone, so the folder's mark stays behind it


def read_against(rows: Sequence[Row], server: Mapping[int, Iterable[str]], waiting: Collection[str]) -> FlagRead:
    """Each of `rows` compared with what the `server` says of its UID. One on its way out by a queued move, or with a flag
    change in `waiting` (its Message-ID), keeps its own flags. A UID the server didn't name changes nothing."""
    held = {r["pk"] for r in rows if r["pending_removal"] is not None or r["message_id"] in waiting}
    changed = [(r, new) for r in rows if r["uid"] in server and r["pk"] not in held
               for new in [columns_of(server[r["uid"]])] if any(new[k] != r[k] for k in COLUMNS)]
    return FlagRead(changed, bool(held))
