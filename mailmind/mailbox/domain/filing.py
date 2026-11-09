"""What a person may ask of their filing: the verbs, the flags a request may set, and how many rows one request may file.
A request's schema names each (`mailmind.mailbox.api`), so the page has them as types, and `BULK_MAX` from the server too
(`mailmind.http.page_limits`), and keeps no copy of its own."""
from collections.abc import Mapping
from typing import Any, Literal, get_args

from mailmind.kernel.errors import Refused
from mailmind.kernel.ids import FolderName, MessageKey
from mailmind.kernel.parsed import as_int, is_list, is_object
from mailmind.kernel.row import Row

type NamedCopy = tuple[MessageKey, FolderName, int | None]      # a message's key, its folder, and its uid there where given
type FilingVerb = Literal["archive", "trash", "junk"]
FILING_VERBS: tuple[FilingVerb, ...] = get_args(FilingVerb.__value__)

# A flag a request may set. \Deleted, \Draft and \Answered are the app's to set (trash, drafts, sends), never a request's:
# \Deleted from one would bypass Trash and Undo.
type FlagName = Literal["flagged", "seen"]
FLAG_NAMES: tuple[FlagName, ...] = get_args(FlagName.__value__)

BULK_MAX = 500          # rows one request may file: the page sends more as more requests, under one undo group


def takes_conversations(verb: str) -> bool:
    """Whether a marked row of this verb may stand for its whole conversation. Junk is said of one sender's message."""
    return verb != "junk"


def has_thread(row: Row) -> bool:
    """Whether the message is in a thread. One without a thread id is a conversation of one, never the folder's unthreaded pool."""
    return bool((row["thread_id"] or "").strip())


def _named(one: Mapping[str, Any]) -> NamedCopy:
    """The copy a marked row names, by its key and folder, and its uid there where the row says it."""
    key, folder, uid = as_int(one.get("key")), one.get("folder"), one.get("uid")
    if key is None or not isinstance(folder, str) or (uid is not None and as_int(uid) is None):
        raise Refused()
    return MessageKey(key), FolderName(folder), None if uid is None else as_int(uid)


def marked_rows(rows: object, limit: int, whole_allowed: bool) -> list[tuple[NamedCopy, bool]]:
    """The copies a request's marked `rows` name, each with whether it stands for its conversation. Up to `limit`: one naming
    more is refused whole, never cut short, since the page has already taken every row off the list."""
    if not is_list(rows) or not rows or len(rows) > limit:
        raise Refused()
    wanted: list[tuple[NamedCopy, bool]] = []
    for one in rows:
        if not is_object(one):
            raise Refused()
        wanted.append((_named(one), one.get("whole") is True and whole_allowed))
    return wanted
