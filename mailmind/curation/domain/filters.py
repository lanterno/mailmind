"""A person's filters: what to do with new mail that fits a search.

A filter is {id, q, star, read}. `q` is the grammar /api/search parses, so creating one from a search is saving the search. Whether
a query can be judged of mail as it arrives, and whether a message fits it, are Discovery's to say. Filters are kept in the rules
file beside the keep and block lists.

A filter stars or marks read, and the server hears of it through the command path. MailMind has no labels of its own, so there is
no auto-label, and in this version no filter moves mail.
"""
import secrets
from collections.abc import Iterable
from typing import Literal, TypedDict

from mailmind.kernel.parsed import is_list, is_object

type FilterVerb = Literal["create", "update", "delete"]        # what a request may do to the filters: its schema names it so
MOST = 100                                                     # each is a query against every new message


class Filter(TypedDict):
    id: str
    q: str
    star: bool
    read: bool


def normalized(raw: object) -> list[Filter]:
    """The filters a file held. Raises ValueError on a shape that isn't filters, as the rest of the file's reading does: a bad hand
    edit keeps the last rules read well, and a save moves the file aside first."""
    if raw is None:
        return []
    if not is_list(raw):
        raise ValueError("not filters")
    out: list[Filter] = []
    for f in raw:
        if not is_object(f) or not isinstance(f.get("id"), str) or not isinstance(f.get("q"), str):
            raise ValueError("not a filter")
        out.append({"id": f["id"], "q": f["q"], "star": bool(f.get("star")), "read": bool(f.get("read"))})
    return out


def new(q: str, star: bool, read: bool) -> Filter:
    return {"id": "f-" + secrets.token_urlsafe(8), "q": q, "star": star, "read": read}


def replaced(filters: Iterable[Filter], changed: Filter) -> list[Filter] | None:
    """The filters with this one as it now reads, or None where there's no filter of its id."""
    items = list(filters)
    if not any(f["id"] == changed["id"] for f in items):
        return None
    return [changed if f["id"] == changed["id"] else f for f in items]


def without(filters: Iterable[Filter], filter_id: str | None) -> list[Filter] | None:
    """The filters without this one, or None where there's no filter of its id."""
    items = list(filters)
    kept = [f for f in items if f["id"] != filter_id]
    return None if len(kept) == len(items) else kept


def flags_of(fits: Iterable[Filter]) -> dict[str, bool]:
    """What the filters a message fits ask of it, together, as the flag columns name them."""
    flags: dict[str, bool] = {}
    for f in fits:
        if f["star"]:
            flags["flagged"] = True
        if f["read"]:
            flags["seen"] = True
    return flags
