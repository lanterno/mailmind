"""What the page asks of Discovery, and what it answers."""
from typing import Any, Literal

from pydantic import Field

from mailmind.http.schemas.common import Answer, QueryArgs, RequestBody


class SearchArgs(QueryArgs):
    """Read whole by `search.asked`. A limit and an offset stay text: one that won't read is refused only once something has been
    typed, so an empty box answers whatever else the request holds."""
    q: str | None = None
    scope: str | None = None
    account: str | None = None
    limit: str | None = None
    offset: str | None = None


class DocumentsArgs(QueryArgs):
    """Read whole by `documents.asked`. A limit or an offset that won't read is its default, since a page in the wrong place harms
    no one, so they stay text too."""
    q: str | None = None
    kind: str | None = None
    limit: str | None = None
    offset: str | None = None


class ContactsArgs(QueryArgs):
    q: str | None = None


class ContactArgs(QueryArgs):
    addr: str | None = None                 # left out is the use case's `bad address`


class ViewChange(RequestBody):
    verb: str | None = None                 # unknown to the use case is its `verb must be create|delete`
    id: str | None = None
    name: str | None = None
    q: str | None = None


class KnownBody(RequestBody):
    addrs: list[Any] | None = None          # left out is `known.asked`'s refusal, and what no mail could go to its `bad` list


class SearchHit(Answer):
    key: int                                # every row has one: `store.init` gives one to a row an older commit wrote
    account: str
    folder: str
    uid: int
    alias: str | None
    from_name: str | None
    from_addr: str | None
    subject: str
    when: str
    date: str
    snippet: str
    seen: bool
    flagged: bool
    has_attach: bool
    is_junk: bool
    bucket: str | None
    thread_id: str | None
    importance: int | None
    action: str | None
    needs_you: bool
    reply_worthy: bool
    commitment_worthy: bool
    safe_to_file: bool


class SearchPage(Answer):
    ok: Literal[True]
    query: str
    scope: str
    items: list[SearchHit]
    offset: int
    has_more: bool
    generated: str


class NothingAsked(Answer):
    ok: Literal[True]
    query: Literal[""]
    items: list[SearchHit]
    total: int
    has_more: bool


class ListedView(Answer):
    id: str
    name: str
    q: str
    builtin: bool
    dynamic: str | None = None              # a built-in whose q the client expands


class Views(Answer):
    ok: Literal[True]
    views: list[ListedView]


class Document(Answer):
    key: int
    folder: str
    uid: int
    part_id: str
    filename: str
    kind: str
    size: int
    account: str
    from_: str = Field(alias="from")
    subject: str
    when: str
    url: str


class Documents(Answer):
    ok: Literal[True]
    total: int
    matched: int
    shown: int
    facets: dict[str, int]
    docs: list[Document]


class Suggestion(Answer):
    addr: str
    name: str
    n: int


class Suggestions(Answer):
    ok: Literal[True]
    items: list[Suggestion]


class RecentMail(Answer):
    key: int
    folder: str
    uid: int
    account: str
    subject: str
    thread_id: str | None
    when: str
    seen: bool


class ContactCard(Answer):
    ok: Literal[True]
    addr: str
    name: str
    count: int
    replied: int
    known: bool
    last_ago: str | None
    first_ago: str | None
    accounts: list[str]
    recent: list[RecentMail]


class KnownVerdict(Answer):
    known: bool
    self: bool


class Known(Answer):
    ok: Literal[True]
    items: dict[str, KnownVerdict]
    bad: list[str]
    thin: bool
