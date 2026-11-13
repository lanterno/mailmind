"""The search grammar, parsed.

Grammar (read into words as shlex reads them, so "quoted phrases" stay intact):
  from:VALUE subject:VALUE body:VALUE to:VALUE cc:VALUE  -> full-text, in that field
  "a phrase"  bareword                            -> full-text, anywhere
  is:unread|read|flagged|answered|junk            -> a flag
  is:newsletter|receipt|notification              -> a bucket, by its shorthand
  is:snoozed                                      -> snoozed mail, which is otherwise out of sight
  bucket:VALUE  category:VALUE                    -> exact
  has:attachment                                  -> has one
  in:FOLDER  account:EMAIL                        -> exact
  label:NAME                                      -> a Gmail label the message carries, in any case
  before:YYYY-MM-DD  after:YYYY-MM-DD             -> a time, at local midnight
  -TOKEN                                          -> negation

The parser is total: no input raises, and input past MAX_CHARS is cut. A `QuerySpec` is what search,
saved views and a bulk command's plan all read a query as, and only an adapter turns one into SQL.

The Rust core parses it (`mm_discovery::query`), as every client does, so a query reads the same in each. A date
with no zone is this machine's, as the person typing it means: before:2026-11-02 ends at that midnight here.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Self

from mailmind_leaves import (
    QUERY_BUCKET_ALIAS,
    QUERY_FIELDS,
    QUERY_FLAGS,
    QUERY_HAS_OFFERED,
    QUERY_IS_OFFERED,
    QUERY_MAX_CHARS,
    QUERY_OPERATORS,
    parse_query,
)

MAX_CHARS = QUERY_MAX_CHARS


class Kind(Enum):
    TEXT = "text"              # value: words to find anywhere
    FIELD = "field"            # field: from, subject, body, to or cc; value: words to find there
    FLAG = "flag"              # field: a name in FLAGS
    SNOOZED = "snoozed"
    BUCKET = "bucket"          # value: the bucket
    CATEGORY = "category"      # value: the AI's category
    ATTACHMENT = "attachment"
    FOLDER = "folder"          # value: its name
    LABEL = "label"            # value: a Gmail label's name
    ACCOUNT = "account"        # value: its address
    BEFORE = "before"          # value: seconds
    AFTER = "after"            # value: seconds


FIELDS = frozenset(QUERY_FIELDS)
FLAGS = frozenset(QUERY_FLAGS)
BUCKET_ALIAS = dict(QUERY_BUCKET_ALIAS)           # shorthand -> the real classifier bucket string
# What the search box offers as you type (domain.suggest), in the order it offers them: the operators this parser reads, and
# the is: and has: values worth a line. BUCKET_ALIAS has several words for most buckets, and one of each is enough to offer.
OPERATORS = QUERY_OPERATORS
IS_OFFERED = QUERY_IS_OFFERED
HAS_OFFERED = QUERY_HAS_OFFERED


@dataclass(frozen=True)
class Clause:
    kind: Kind
    value: str | int | None = None
    field: str | None = None
    negated: bool = False
    narrows: bool = False       # asked for, it picks out part of the mailbox


@dataclass(frozen=True)
class QuerySpec:
    clauses: tuple[Clause, ...] = ()

    @classmethod
    def parse(cls, text: str | None) -> Self:
        return cls(tuple(Clause(Kind(kind), value, field, negated, narrows)
                         for kind, value, field, negated, narrows in parse_query(text)))

    def narrows(self) -> bool:
        """Whether it picks out part of the mailbox, which a bulk command must. A folder or an account
        alone spans nearly everything, and a negation never narrows on its own."""
        return any(c.narrows and not c.negated for c in self.clauses)

    def names_folder(self) -> bool:
        return any(c.kind is Kind.FOLDER and not c.negated for c in self.clauses)

    def asks_about_snoozed(self) -> bool:
        return any(c.kind is Kind.SNOOZED for c in self.clauses)
