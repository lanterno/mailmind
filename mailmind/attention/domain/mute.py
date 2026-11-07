"""A mute: a thread silenced, here and on the server (decision D6). What it marks read, Undo marks unread again, and no
more than that."""
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from mailmind.kernel.row import Row


@dataclass(frozen=True)
class MutePlan:
    """What muting a thread takes: the unread members to mark read, and the undo entry that remembers them."""
    mark_read: Sequence[Row]
    undo: dict[str, Any]


def plan(already_muted: bool, account: str, thread_id: str, unread: Sequence[Row], member: Row | None) -> MutePlan | None:
    """None if the thread is muted already: a repeat is a no-op, for by then its mail reads as all seen, and an Undo that
    remembered that would mark nothing unread. The entry names what it marked by key and folder, since a pk isn't stable,
    and one member, which can say later which thread this has become."""
    if already_muted:
        return None
    return MutePlan(unread, {"kind": "mute", "account": account, "thread_id": thread_id,
                             "unread": [[r["message_key"], r["folder"]] for r in unread], "label": "Muted",
                             "member": [member["message_key"], member["folder"]] if member else None})


def threads_to_unmute(entry: Mapping[str, Any], thread_now: Iterable[str]) -> set[str]:
    """A merge moves a mute to the thread that took this one in, and an entry doesn't hear of it: the thread it names, and
    the one its member is in now."""
    return {entry["thread_id"], *thread_now}


def seen_to_restore(entry: Mapping[str, Any]) -> list[tuple[int, int]]:
    """(pk, seen) of an entry from when muting told the server nothing, which kept each member's `prev_seen`. One of today's
    has none, and goes by `unread`."""
    return [(pk, int(prev or 0)) for pk, prev in entry.get("prev_seen", [])]


def marked_read(entry: Mapping[str, Any]) -> list[Sequence[Any]]:
    """The (key, folder) of each member the mute marked read, which Undo marks unread again."""
    return entry.get("unread", [])
