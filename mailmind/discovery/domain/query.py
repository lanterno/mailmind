"""The search grammar, parsed.

Grammar (tokenized with shlex so "quoted phrases" stay intact):
  from:VALUE subject:VALUE body:VALUE to:VALUE cc:VALUE  -> full-text, in that field
  "a phrase"  bareword                            -> full-text, anywhere
  is:unread|read|flagged|answered|junk            -> a flag
  is:newsletter|receipt|notification              -> a bucket, by its shorthand
  is:snoozed                                      -> snoozed mail, which is otherwise out of sight
  bucket:VALUE  category:VALUE                    -> exact
  has:attachment                                  -> has one
  in:FOLDER  account:EMAIL                        -> exact
  before:YYYY-MM-DD  after:YYYY-MM-DD             -> a time, at local midnight
  -TOKEN                                          -> negation

The parser is total: no input raises, and input past MAX_CHARS is cut. A `QuerySpec` is what search,
saved views and a bulk command's plan all read a query as, and only an adapter turns one into SQL.
"""
import shlex
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Self

MAX_CHARS = 512


class Kind(Enum):
    TEXT = "text"              # value: words to find anywhere
    FIELD = "field"            # field: from, subject, body, to or cc; value: words to find there
    FLAG = "flag"              # field: a name in FLAGS
    SNOOZED = "snoozed"
    BUCKET = "bucket"          # value: the bucket
    CATEGORY = "category"      # value: the AI's category
    ATTACHMENT = "attachment"
    FOLDER = "folder"          # value: its name
    ACCOUNT = "account"        # value: its address
    BEFORE = "before"          # value: seconds
    AFTER = "after"            # value: seconds


FIELDS = frozenset({"from", "subject", "body", "to", "cc"})
FLAGS = frozenset({"unread", "read", "flagged", "answered", "junk"})
BUCKET_ALIAS = {                                  # shorthand -> the real classifier bucket string
    "newsletter": "newsletters/marketing", "newsletters": "newsletters/marketing",
    "marketing": "newsletters/marketing",
    "receipt": "receipts/transactional", "receipts": "receipts/transactional",
    "transactional": "receipts/transactional",
    "notification": "automated-notifications", "notifications": "automated-notifications",
    "automated": "automated-notifications",
    "spam": "spam/scam", "scam": "spam/scam", "blocked": "blocked",
    "personal": "personal/protected", "protected": "personal/protected", "other": "other/personal-ish",
}
# is:<x> values that pick out part of the mailbox: a content lane or a minority flag. Not "read", which
# is usually most mail, though its inverse "unread" is.
_NARROW_IS = frozenset({"newsletter", "newsletters", "marketing", "receipt", "receipts", "transactional",
                        "notification", "notifications", "automated", "spam", "scam", "flagged", "unread", "junk"})


@dataclass(frozen=True)
class Clause:
    kind: Kind
    value: str | int | None = None
    field: str | None = None
    negated: bool = False
    narrows: bool = False       # asked for, it picks out part of the mailbox


def _seconds(text: str) -> int | None:
    """A date or time as seconds. With no zone it means this machine's, as the person typing it does:
    before:2026-11-02 ends at that midnight here, not at UTC's."""
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except Exception:
        return None


def _clause(token: str) -> Clause | None:
    negated = token.startswith("-") and len(token) > 1
    if negated:
        token = token[1:]
    name, sep, value = token.partition(":")
    name, lower = name.lower(), value.lower()
    if sep and name in FIELDS and value:
        return Clause(Kind.FIELD, value, name, negated, narrows=True)
    if sep and name == "is" and lower in FLAGS:
        return Clause(Kind.FLAG, None, lower, negated, narrows=lower in _NARROW_IS)
    if sep and name == "is" and lower == "snoozed":
        return Clause(Kind.SNOOZED, negated=negated)
    if sep and name == "is" and lower in BUCKET_ALIAS:
        return Clause(Kind.BUCKET, BUCKET_ALIAS[lower], None, negated, narrows=lower in _NARROW_IS)
    if sep and name == "bucket" and value:
        return Clause(Kind.BUCKET, BUCKET_ALIAS.get(lower, lower), None, negated, narrows=True)
    if sep and name == "category" and value:
        return Clause(Kind.CATEGORY, lower, None, negated, narrows=True)
    if sep and name == "has" and lower == "attachment":
        return Clause(Kind.ATTACHMENT, negated=negated, narrows=True)
    if sep and name == "in" and value:
        return Clause(Kind.FOLDER, value, None, negated)
    if sep and name == "account" and value:
        return Clause(Kind.ACCOUNT, value, None, negated)
    if sep and name in ("before", "after") and value:
        seconds = _seconds(value)
        if seconds is None:                 # says nothing: not a word to search for either
            return None
        return Clause(Kind.BEFORE if name == "before" else Kind.AFTER, seconds, None, negated, narrows=True)
    if token.strip():
        return Clause(Kind.TEXT, token, None, negated, narrows=True)
    return None


@dataclass(frozen=True)
class QuerySpec:
    clauses: tuple[Clause, ...] = ()

    @classmethod
    def parse(cls, text: str | None) -> Self:
        text = (text or "").strip()[:MAX_CHARS]
        try:
            tokens = shlex.split(text)
        except ValueError:                  # an unbalanced quote and the like
            tokens = text.split()
        return cls(tuple(c for c in map(_clause, tokens) if c))

    def narrows(self) -> bool:
        """Whether it picks out part of the mailbox, which a bulk command must. A folder or an account
        alone spans nearly everything, and a negation never narrows on its own."""
        return any(c.narrows and not c.negated for c in self.clauses)

    def names_folder(self) -> bool:
        return any(c.kind is Kind.FOLDER and not c.negated for c in self.clauses)

    def asks_about_snoozed(self) -> bool:
        return any(c.kind is Kind.SNOOZED for c in self.clauses)
