"""Saved searches ("Views"): named, pinned search-grammar queries shown in the folder rail.

A view is just {id, name, q} where q is a raw search-grammar string (the same grammar /api/search parses), so a saved
search and an auto-label "lane" are the same thing. Built-in DEFAULTS and LANES are merged in read-only at load time:
they always appear, can't be deleted, and are never written to disk.
"""
import secrets
from collections.abc import Iterable
from typing import Literal, NotRequired, TypedDict

from mailmind.kernel.parsed import is_list, is_object

type ViewVerb = Literal["create", "delete"]          # what a request may do to the views: its schema names it so


class View(TypedDict):
    id: str
    name: str
    q: str
    dynamic: NotRequired[str]    # a built-in whose q the client expands


class ListedView(View):
    builtin: bool


# Built-in smart defaults (always present, non-deletable). q uses the live search grammar; the
# __LAST7__ sentinel is expanded to after:<date> client-side so the FIXED grammar stays untouched.
DEFAULTS: tuple[View, ...] = (
    {"id": "__unread__", "name": "Unread", "q": "is:unread"},
    {"id": "__flagged__", "name": "Flagged", "q": "is:flagged"},
    {"id": "__attach__", "name": "Has attachments", "q": "has:attachment"},
    {"id": "__last7__", "name": "Last 7 days", "q": "__LAST7__", "dynamic": "last7"},
)
# Auto-label lanes (registered by the lanes feature) — also built-in, non-deletable, non-persisted.
LANES: tuple[View, ...] = (
    {"id": "__lane_feed__", "name": "Feed", "q": "is:newsletter"},
    {"id": "__lane_papertrail__", "name": "Paper trail", "q": "is:receipt"},
    {"id": "__lane_notifications__", "name": "Notifications", "q": "is:notification"},
    {"id": "__lane_action__", "name": "Action needed", "q": "category:action-needed"},
    {"id": "__lane_finance__", "name": "Finance", "q": "category:finance"},
)
BUILTIN_IDS = {v["id"] for v in DEFAULTS} | {v["id"] for v in LANES}


def clean(raw: object) -> list[View]:
    """User views out of whatever a file held (no builtins). TOTAL: never raises, drops malformed entries."""
    out: list[View] = []
    for v in (raw if is_list(raw) else []):
        if not is_object(v):
            continue
        vid, name, q = str(v.get("id", "")), str(v.get("name", "")).strip(), str(v.get("q", "")).strip()
        if not vid or not name or not q or vid in BUILTIN_IDS:
            continue
        out.append({"id": vid, "name": name[:60], "q": q[:512]})
    return out


def merged(user_views: Iterable[View], include_lanes: bool = True) -> list[ListedView]:
    """What the API + client see: DEFAULTS, then lanes, then user views — builtins flagged."""
    builtins = list(DEFAULTS) + (list(LANES) if include_lanes else [])
    listed: list[ListedView] = [{**v, "builtin": True} for v in builtins]
    return listed + [{**v, "builtin": False} for v in user_views]


def new(name: str | None, q: str | None) -> View | None:
    """A user view, or None without a name and a query."""
    name, q = (name or "").strip()[:60], (q or "").strip()[:512]
    if not name or not q:
        return None
    return {"id": "v" + secrets.token_hex(4), "name": name, "q": q}


def without(user_views: Iterable[View], view_id: str | None) -> list[View] | None:
    """The views with this one gone, or None if that changes nothing. A built-in or a lane can't go, whatever a file holds."""
    if view_id in BUILTIN_IDS:
        return None
    cur = list(user_views)
    kept = [v for v in cur if v["id"] != view_id]
    return kept if len(kept) != len(cur) else None


