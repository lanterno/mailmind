"""Which message a try at a command acts on. A UID means something only in the epoch (UIDVALIDITY) it was seen in. On a first
try the one seen as the command was accepted is it. A later try may follow one that moved the message, or a renumbering, so
the Message-ID says, and a folder with none by that id means the message has left it, whatever the UID now names."""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Target:
    by_message_id: bool         # ask the server which UID the Message-ID names, and believe it
    uid: int | None             # what to act on otherwise, or if it won't search; None: the message has left


def is_first_try(just_accepted: bool, begun: object) -> bool:
    """Whether no earlier try can have reached the server."""
    return just_accepted or not begun


def same_epoch(epoch: object, uidvalidity: int | None) -> bool:
    """Whether a UID seen in `epoch` still means the same message, as far as either side says."""
    return not epoch or not uidvalidity or epoch == uidvalidity


def of_try(*, uid: Any, epoch: object, uidvalidity: int | None, first_try: bool, has_message_id: bool) -> Target:
    """What a try acts on: `uid` and `epoch` as the command was accepted, `uidvalidity` as the select just said."""
    seen = int(uid) if uid else None
    same = same_epoch(epoch, uidvalidity)
    return Target(by_message_id=has_message_id and not (first_try and seen and same), uid=seen if same else None)
