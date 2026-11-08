"""What a person may ask of their filing: the verbs, the flags a request may set, and how many rows one request may file.
A request's schema names each (`mailmind.mailbox.api`), so the page has them as types, and `BULK_MAX` from the server too
(`mailmind.http.page_limits`), and keeps no copy of its own."""
from typing import Literal, get_args

type FilingVerb = Literal["archive", "trash", "junk"]
FILING_VERBS: tuple[FilingVerb, ...] = get_args(FilingVerb.__value__)

# A flag a request may set. \Deleted, \Draft and \Answered are the app's to set (trash, drafts, sends), never a request's:
# \Deleted from one would bypass Trash and Undo.
type FlagName = Literal["flagged", "seen"]
FLAG_NAMES: tuple[FlagName, ...] = get_args(FlagName.__value__)

BULK_MAX = 500          # rows one request may file: the page sends more as more requests, under one undo group
