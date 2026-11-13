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

from collections.abc import Iterable as _Iterable
from typing import final as _final

@_final
class RuleSets:
    """The person's keep and block lists, held by the core, built once when the rules change."""

    def __new__(
        cls,
        keep_senders: _Iterable[str],
        block_senders: _Iterable[str],
        keep_domains: _Iterable[str],
        block_domains: _Iterable[str],
    ) -> RuleSets: ...

def classify(from_addr: str, subject: str, from_name: str, has_list_unsub: bool, rules: RuleSets) -> tuple[str, str, str]:
    """`(bucket, confidence, reason)` for a message: its From address, subject and display name, and whether it carried
    a List-Unsubscribe or List-ID header. The rules come first."""
    ...

def looks_human(local: str | None) -> bool:
    """Whether a local part looks like a person's, not a role's or a mailer's."""
    ...

def by_rule(reason: str | None) -> bool:
    """Whether a reason `classify` gave says one of the person's rules decided."""
    ...

def is_junk(bucket: str | None) -> bool:
    """Whether a bucket, by its name, is junk."""
    ...


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


# -- MIME (mm-content, src/mime.rs) ------------------------------------------------------------------------------

def mime_parse(
    raw: bytes,
) -> tuple[str | None, str | None, list[tuple[int, str, str, int, str | None, bool, bool]], str, str, str, str]:
    """A raw message read: (text, html, attachments, to, cc, reply_to, date), each attachment (part_id, filename,
    content_type, size, content_id, is_inline, travels). `html` is not made safe yet."""
    ...

def mime_extract_part(raw: bytes, part_id: int) -> tuple[bytes, str] | None:
    """The leaf `part_id` names, as mime_parse numbered it: (its bytes, its media type), or None."""
    ...

def mime_inline_parts(raw: bytes) -> list[tuple[str, str, bytes]]:
    """Every leaf with a Content-ID, as (content_id, content_type, bytes); a Content-ID given twice is the later part's."""
    ...

# -- the HTML sanitizer (mm-content, src/sanitize.rs) ---------------------------------------------------------------
