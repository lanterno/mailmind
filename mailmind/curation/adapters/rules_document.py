"""The rule book's one reader and writer: the `rules` document in the database (mailmind.platform.db.documents), which is
encrypted at rest.

It holds the keep and block lists, each sender's shelf life and the filters, as JSON in the shape mailmind_rules.json held,
which nothing reads any more. A read parses it again only when its version moved. A change reads, changes and writes it back
in one write transaction, which takes the database's write lock before it reads, so two at once, from the command line and
the server or from two threads of either, lose neither: the second reads what the first wrote. A write is whole or nothing.

`python -m mailmind rules` lists and changes them.
"""
import copy
import json
from collections.abc import Callable

from mailmind.curation.domain import rulebook
from mailmind.curation.domain.rulebook import EXPIRE, KEYS, RuleSets, Rules
from mailmind.kernel.row import Connection
from mailmind.platform import settings
from mailmind.platform.db import documents
from mailmind.platform.db.uow import UnitOfWork

__all__ = ["EXPIRE", "KEYS", "NAME", "add", "change", "load", "remove", "save", "set_expiry", "sets"]

NAME = "rules"                  # the document's name in `documents`

_uow = UnitOfWork()
_last: tuple[str, int, Rules, RuleSets] | None = None    # (database, version, rules, sets): the rules last parsed


def _parsed(body: str) -> Rules:
    return rulebook.normalized(json.loads(body))


def _read() -> tuple[Rules, RuleSets]:
    """(rules, sets) as the rule book stands now, parsed again only when its version moved."""
    global _last
    db = settings.current().db
    with _uow.read() as c:
        version = documents.version(c, NAME)
        last = _last
        if last is not None and last[:2] == (db, version):
            return last[2], last[3]
        doc = documents.stored(c, NAME) if version is not None else None
    if doc is None:
        return rulebook.empty(), rulebook.no_sets()
    rules = _parsed(doc.body)
    sets = rulebook.as_sets(rules)
    _last = (db, doc.version, rules, sets)
    return rules, sets


def load() -> Rules:
    """The rules, as lists (and expiry hours) of the caller's own to change."""
    return copy.deepcopy(_read()[0])


def sets() -> RuleSets:
    """The keep and block lists as frozensets, for the classifier: shared, never changed."""
    return _read()[1]


def _held(c: Connection) -> Rules:
    """The rules as they stand, read inside a change's transaction."""
    doc = documents.stored(c, NAME)
    return rulebook.empty() if doc is None else _parsed(doc.body)


def _write(c: Connection, rules: Rules) -> None:
    documents.write(c, NAME, rulebook.for_saving(rules))


def save(rules: Rules) -> None:
    """Put `rules` in place of the rule book, whole."""
    with _uow.write() as c:
        _write(c, rules)


def change[T](apply: Callable[[Rules], T]) -> T:
    """One change to the rules, as one step: `apply` changes a copy, and what it returns comes back. If it raises, nothing is
    saved."""
    with _uow.write() as c:
        rules = _held(c)
        out = apply(rules)
        _write(c, rules)
    return out


def add(token: str, verdict) -> tuple[str, str]:
    """verdict in {'keep','block'}. Routes a sender/domain into the right list
    and removes it from the opposite verdict to avoid contradictions."""
    token = rulebook.token_of(token)
    return token, change(lambda rules: rulebook.add(rules, token, verdict))


def remove(token: str) -> None:
    """Drop a keep/block entry. Leaves any expiry rule alone."""
    token = rulebook.token_of(token)
    change(lambda rules: rulebook.remove(rules, token))


def set_expiry(token: str, hours: int | str) -> tuple[str, int]:
    """Expire INBOX mail from `token` (sender or domain) after `hours`; 0 clears."""
    token = rulebook.token_of(token)
    hours = int(hours)
    change(lambda rules: rulebook.set_expiry(rules, token, hours))
    return token, hours
