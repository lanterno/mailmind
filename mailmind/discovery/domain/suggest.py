"""What the search box offers as you type: the word being typed, finished from the grammar and from who has written to you.

The box is read here as the parser reads it, and a suggestion says how the whole box reads with it taken, so the page never works
out where a word starts or which operators there are.
"""
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from mailmind.discovery.domain.query import HAS_OFFERED, IS_OFFERED, MAX_CHARS, OPERATORS
from mailmind.kernel.row import Row

_PEOPLE = frozenset({"from", "to", "cc"})
_NOT_YET = 2                                # characters of a bare word before who has written is worth reading through
_NEEDLE = 64
_SENDERS, _DOMAINS = 5, 3                   # how many of each a popover has room for
_UNQUOTED = re.compile(r"[^\s\"']+")        # what a query can hold without quoting it: a sender's address is a stranger's text


@dataclass(frozen=True)
class Typing:
    settled: str            # the box before the word being typed, as it was typed
    minus: str              # "-" where the word negates
    name: str | None        # the operator it has got as far as naming
    value: str              # what follows the operator's colon, else the whole word


def typing(q: str | None) -> Typing | None:
    """The word still being typed, or None with nothing to finish: an empty box, a space just typed, a question or a command
    (`?` and `!` aren't searches), an open quote, or a word with a colon the grammar doesn't read."""
    text = (q or "").lstrip()
    if not text or len(text) > MAX_CHARS or text[0] in "?!" or text[-1].isspace():
        return None
    start, quoted = 0, False
    for i, ch in enumerate(text):
        if ch == '"':
            quoted = not quoted
        elif ch.isspace() and not quoted:
            start = i + 1
    word = text[start:]
    if '"' in word or "'" in word:
        return None
    minus = "-" if word.startswith("-") and len(word) > 1 else ""
    name, colon, value = word[len(minus):].partition(":")
    if colon and name.lower() not in OPERATORS:
        return None
    return Typing(text[:start], minus, name.lower(), value) if colon else Typing(text[:start], minus, None, name)


def needle(t: Typing) -> str | None:
    """What to read through who has written for, or None where they have no part to play."""
    if t.name in _PEOPLE or (t.name is None and len(t.value) >= _NOT_YET):
        return t.value.strip().lower()[:_NEEDLE]
    return None


def _offer(t: Typing, finished: str, label: str, detail: str, complete: bool) -> dict[str, Any]:
    return {"value": t.settled + t.minus + finished + (" " if complete else ""), "label": label, "detail": detail,
            "complete": complete}


def _values(t: Typing, name: str, values: tuple[str, ...]) -> list[dict[str, Any]]:
    return [_offer(t, f"{name}:{v}", f"{name}:{v}", "", True) for v in values if v.startswith(t.value.lower())]


def offers(t: Typing, senders: Iterable[Row], domains: Iterable[Row]) -> list[dict[str, Any]]:
    """The suggestions for this word, each with `value`, the whole box as it reads with this one taken, and `complete`, whether
    that can be searched as it stands or an operator is waiting for its value."""
    if t.name == "is":
        return _values(t, "is", IS_OFFERED)
    if t.name == "has":
        return _values(t, "has", HAS_OFFERED)
    out: list[dict[str, Any]] = []
    if t.name is None:
        out += [_offer(t, f"{n}:", f"{n}:", "", False) for n in OPERATORS if n.startswith(t.value.lower())]
    field = t.name or "from"
    for r in list(senders)[:_SENDERS]:
        if _UNQUOTED.fullmatch(r["addr"] or ""):
            out.append(_offer(t, f"{field}:{r['addr']}", r["name"] or r["addr"], r["addr"] if r["name"] else "", True))
    for r in list(domains)[:_DOMAINS]:
        if _UNQUOTED.fullmatch(r["domain"] or ""):
            out.append(_offer(t, f"{field}:{r['domain']}", r["domain"], "everyone at this domain", True))
    return out
