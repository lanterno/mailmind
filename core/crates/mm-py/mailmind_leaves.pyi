"""The leaves the core shares with the Python app (core/crates/mm-py)."""

# -- message identity (mm-kernel) ---------------------------------------------------------------------------------

def identity_of(message_id: str | None, from_addr: str | None, date_iso: str | None, subject: str | None) -> str:
    """A message's identity: its Message-ID without whitespace, or `~` and the SHA-256 of its envelope."""
    ...


# -- the search grammar (mm-discovery, src/query.rs) ---------------------------------------------------------------

QUERY_MAX_CHARS: int
QUERY_FIELDS: tuple[str, ...]
QUERY_FLAGS: tuple[str, ...]
QUERY_BUCKET_ALIAS: dict[str, str]
QUERY_OPERATORS: tuple[str, ...]
QUERY_IS_OFFERED: tuple[str, ...]
QUERY_HAS_OFFERED: tuple[str, ...]

def parse_query(text: str | None) -> list[tuple[str, str | int | None, str | None, bool, bool]]:
    """A query's clauses, each (kind, value, field, negated, narrows). A date with no zone is in this process's own."""
    ...


# -- the classifier (mm-curation, src/classifier.rs) ---------------------------------------------------------------



# -- threading (mm-mailbox, src/threading.rs) ----------------------------------------------------------------------
# A copy crosses as (pk, message_id, gmail_thread, in_reply_to, refs, thread_id, folder, ts), and a conversation as
# (root, its copies, the ids it holds). A placement comes back as `given` and `merged`, pairs in their dicts' order.

type ThreadCopy = tuple[int, str | None, str | None, str | None, str | None, str | None, str, int | None]
type ThreadPlacement = tuple[list[tuple[int, str]], list[tuple[str, str]]]

THREAD_REFERENCES_MAX: int
"""How many ids of a References header count, nearest first."""

def thread_thrid(gmail_thread: str | None) -> str:
    """Gmail's thread id for a copy, stripped, or empty."""
    ...

def thread_key(pk: int, message_id: str | None) -> str:
    """A copy's own id, or `pk:` and its pk when it has no usable one."""
    ...

def thread_references(in_reply_to: str | None, refs: str | None) -> list[str]:
    """The ids a copy says it follows: In-Reply-To's, then the last of References'."""
    ...

def thread_names(message_id: str | None, gmail_thread: str | None, in_reply_to: str | None, refs: str | None) -> list[str]:
    """Everything another copy can meet a copy by, sorted."""
    ...

def thread_conversations(
    account: str, arrivals: list[ThreadCopy], touching: list[tuple[str, list[str]]],
) -> list[tuple[str, list[int], list[str]]]:
    """The conversations among `arrivals`, in the order their first copies came: each one's root, the positions of
    its copies in `arrivals`, and the ids of the threads it holds or touches."""
    ...

def thread_contested(account: str, conversations: list[tuple[str, list[ThreadCopy], list[str]]]) -> list[str]:
    """The Gmail threads whose copies `thread_place` must see, sorted."""
    ...

def thread_place(
    account: str, conversations: list[tuple[str, list[ThreadCopy], list[str]]], members: list[ThreadCopy],
) -> ThreadPlacement:
    """Where each conversation's copies go, and which ids that absorbs."""
    ...

def thread_regroup(account: str, copies: list[ThreadCopy]) -> ThreadPlacement:
    """Every copy an account holds, regrouped from what each names."""
    ...
