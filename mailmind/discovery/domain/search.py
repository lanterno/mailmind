"""What a search decides around its QuerySpec: what a request asks, one result a message, a page of them and how each reads."""
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from mailmind.discovery.domain.query import QuerySpec
from mailmind.kernel import attention
from mailmind.kernel.parsed import required_int
from mailmind.kernel.row import Row

NO_SUBJECT = "(no subject)"


@dataclass(frozen=True)
class Asked:
    q: str
    spec: QuerySpec
    scope: str
    account: str | None         # the one account searched: None under every account, whichever the request named
    sort: str
    limit: int
    offset: int


def asked(qs: Mapping[str, list[str]]) -> Asked | None:
    """What a request asks, or None with nothing typed. A limit or an offset that won't read is refused only after that, so
    an empty box answers whatever else the request holds."""
    q = (qs.get("q", [""])[0] or "").strip()
    if not q:
        return None
    scope = qs.get("scope", ["account"])[0]
    return Asked(q, QuerySpec.parse(q), scope, None if scope == "all" else qs.get("account", [None])[0],
                 qs.get("sort", ["rank"])[0],
                 max(1, min(200, required_int(qs.get("limit", ["80"])[0]))), max(0, required_int(qs.get("offset", ["0"])[0])))


def nothing_asked() -> dict[str, Any]:
    return {"ok": True, "query": "", "items": [], "total": 0, "has_more": False}


def bad_query() -> dict[str, Any]:
    return {"ok": False, "error": "bad query"}


def first_copies(rows: Iterable[Row], limit: int, offset: int) -> list[Row]:
    """One result a message, out of rows in the order to show with the copy to show first: after `offset` of them, up to one
    more than `limit`, which says there is more. It reads no further than that, so newest first stops early. A row with no key
    is a message of its own."""
    out: list[Row] = []
    seen: set[int] = set()
    for r in rows:
        key = r["message_key"] or -r["pk"]
        if key not in seen:
            seen.add(key)
            if len(seen) > offset:
                out.append(r)
            if len(out) > limit:
                break
    return out


def hit(r: Row, aliases: Mapping[str, str | None], when: Callable[[str | None], str]) -> dict[str, Any]:
    """A result as the list shows it. What the AI's verdict asks of you is the kernel's to say."""
    return {
        "pk": r["pk"], "key": r["message_key"], "account": r["account"], "folder": r["folder"],
        "alias": aliases.get(r["account"]),
        "from_name": r["from_name"] or r["from_addr"], "from_addr": r["from_addr"],
        "subject": r["subject"] or NO_SUBJECT, "when": when(r["date_iso"]),
        "date": (r["date_iso"] or "")[:10],
        "snippet": (r["snippet"] or "")[:200], "seen": bool(r["seen"]), "flagged": bool(r["flagged"]),
        "has_attach": bool(r["has_attach"]), "is_junk": bool(r["is_junk"]), "bucket": r["bucket"], "thread_id": r["thread_id"],
        "importance": r["importance"], "action": r["action"], **attention.answers(r["importance"], r["category"], r["action"]),
    }


def page(rows: list[Row], want: Asked, aliases: Mapping[str, str | None], when: Callable[[str | None], str],
         generated: str) -> dict[str, Any]:
    """The answer, from what the index found: up to one row past the page, which says there is more."""
    return {"ok": True, "query": want.q, "scope": want.scope, "items": [hit(r, aliases, when) for r in rows[:want.limit]],
            "offset": want.offset, "has_more": len(rows) > want.limit, "generated": generated}


def reached(rows: list[Row], sample_size: int,
            when: Callable[[str | None], str]) -> tuple[list[tuple[int, str]], list[dict[str, str]]]:
    """What a bulk command's filter holds: each message by key and folder, and the few a preview shows."""
    return ([(r["message_key"], r["folder"]) for r in rows],
            [{"from": (r["from_name"] or r["from_addr"] or ""), "subject": r["subject"] or NO_SUBJECT,
              "when": when(r["date_iso"])} for r in rows[:sample_size]])
