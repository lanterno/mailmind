"""A person as your mail knows them: what autocomplete looks for and how a contact card reads."""
from collections.abc import Callable, Iterable
from typing import Any

from mailmind.kernel.row import Row


def needle(q: str | None) -> str:
    """What autocomplete looks for: the start of what was typed is plenty."""
    return (q or "").strip().lower()[:64]


def suggestion(r: Row) -> dict[str, Any]:
    return {"addr": r["addr"], "name": r["name"] or "", "n": r["n"]}


def address(addr: str | None) -> str | None:
    """The address a card is for, or None for what couldn't be one."""
    a = (addr or "").strip().lower()
    return a if "@" in a else None


def card(a: str, agg: Row, accounts: list[str], recent: Iterable[Row], ago: Callable[[str], str],
         when: Callable[[str | None], str]) -> dict[str, Any]:
    """Relationship memory for one sender: interaction stats, which accounts they write to and recent threads. `ago` and `when`
    say a date as the card and a list do."""
    n = agg["n"] or 0
    return {"ok": True, "addr": a, "name": (agg["name"] or ""), "count": n,
            "replied": agg["replied"] or 0, "known": n > 0,
            "last_ago": (ago(agg["last"]) if agg["last"] else None),
            "first_ago": (ago(agg["first"]) if agg["first"] else None),
            "accounts": accounts,                      # raw emails; client renders via nameOf()
            "recent": [{"pk": r["pk"], "key": r["message_key"], "folder": r["folder"], "account": r["account"], "subject": r["subject"] or "(no subject)",
                        "thread_id": r["thread_id"], "when": when(r["date_iso"]), "seen": bool(r["seen"])}
                       for r in recent]}
