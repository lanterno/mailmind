"""What a row of a list says: a folder's, a conversation's, and the views that aren't folders (Snoozed, Muted, Waiting, Reply
Later). The page reads these answers and never works them out. A list reads no clock here: its use case hands in how a time
is said (`kernel.when`)."""
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from mailmind.kernel import attention as verdict
from mailmind.kernel.row import Row

type When = Callable[[str | None], str]
type Ago = Callable[[float | str | None], str]
type Wakes = Callable[[float | str], str]

SNIPPET_CHARS = 200
SUMMARY_CHARS = 300


def _attention(r: Row) -> dict[str, bool]:
    """What the verdict on row `r` asks of you."""
    return verdict.answers(r["importance"], r["category"], r["action"])


def _who(r: Row) -> dict[str, Any]:
    """Which copy this is and who it is from."""
    return {"key": r["message_key"], "folder": r["folder"], "uid": r["uid"], "from_name": r["from_name"] or r["from_addr"],
            "from_addr": r["from_addr"], "subject": r["subject"] or "(no subject)"}


def _marks(r: Row) -> dict[str, Any]:
    """What the row shows beside its sender."""
    return {"snippet": (r["snippet"] or "")[:SNIPPET_CHARS], "seen": bool(r["seen"]), "flagged": bool(r["flagged"]),
            "has_attach": bool(r["has_attach"]), "is_junk": bool(r["is_junk"]), "bucket": r["bucket"]}


def window(items: Sequence[object], total: int, offset: int) -> dict[str, Any]:
    """Where a page of `items` stands in the `total`."""
    return {"total": total, "offset": offset, "has_more": offset + len(items) < total}


def listed_item(r: Row, group: bool, when: When) -> dict[str, Any]:
    """A row of a list of a folder or of the Inboxes, one conversation's latest message if `group`."""
    it: dict[str, Any] = {
        **_who(r), "when": when(r["date_iso"]), **_marks(r), "importance": r["importance"], **_attention(r),
        "thread_id": r["thread_id"], "summary": (r["summary"] or "")[:SUMMARY_CHARS], "action": r["action"] or "",
        "category": r["category"], "ts": r["ts"], "account": r["account"],
    }
    if group:
        it.update({"count": r["cnt"], "thread_count": r["thread_count"] or r["cnt"], "seen": not r["unread"],
                   "flagged": bool(r["any_flagged"]), "has_attach": bool(r["any_attach"])})
    return it


def snoozed_item(r: Row, wakes: Wakes) -> dict[str, Any]:
    return {**_who(r), "when": "wakes " + wakes(r["snoozed_until"]), "snoozed_until": r["snoozed_until"], **_marks(r),
            "importance": r["importance"], **_attention(r), "thread_id": r["thread_id"], "account": r["account"]}


def muted_item(r: Row, when: When) -> dict[str, Any]:
    """A muted thread's latest message, which counts as read whatever its flag says."""
    return {**_who(r), "when": when(r["date_iso"]), **_marks(r), "seen": True, "thread_id": r["thread_id"], "ts": r["ts"],
            "count": r["thread_count"], "muted": True, "account": r["account"]}


def waiting_item(r: Row, when: When, ago: Ago) -> dict[str, Any]:
    """A thread waiting on someone: on the other side if it was sent (who is the first address it went to), else on you."""
    if r["f_kind"] == "sent":
        label = "Sent " + ago(r["f_first_sent"]) + " ago · no reply"
    else:
        label = "Awaiting your reply · " + when(r["date_iso"])
    return {**_who(r), "when": when(r["date_iso"]), **_marks(r), "thread_id": r["thread_id"], "ts": r["ts"],
            "count": r["thread_count"], "waiting": True, "account": r["account"], "waiting_label": label, "kind": r["f_kind"],
            "to": (r["recipients"] or "").split(",")[0].strip()}


def replylater_item(r: Row, when: When) -> dict[str, Any]:
    return {**_who(r), "when": when(r["date_iso"]), **_marks(r), "thread_id": r["thread_id"], "ts": r["ts"],
            "count": r["thread_count"], "replylater": True, "account": r["account"], "waiting_label": "Reply later"}


def thread_items(rows: Iterable[Row], when: When) -> list[dict[str, Any]]:
    """A conversation's messages as it opens, each once: a Gmail copy in the Inbox and in All Mail is one message. One with no
    Message-ID has nothing to be the same as."""
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for r in rows:
        key = (r["message_id"] or "").strip() or f"pk:{r['pk']}"
        if key in seen_ids:
            continue
        seen_ids.add(key)
        items.append({**_who(r), "when": when(r["date_iso"]), "snippet": (r["snippet"] or "")[:SNIPPET_CHARS],
                      "seen": bool(r["seen"]), "flagged": bool(r["flagged"]), "answered": bool(r["answered"]),
                      "has_attach": bool(r["has_attach"]), "is_junk": bool(r["is_junk"])})
    return items
