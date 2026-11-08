"""What one sender has been writing about, with no I/O: which mail is read, how it is told to the model, and what is taken from
its answer."""
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from mailmind.kernel.parsed import is_list
from mailmind.kernel.row import Row

RECENT = 24                 # how many of a sender's newest messages are read
THREADS = 8                 # how many of their threads, the newest
CAP_TOTAL = 6000            # of everything the model is told
SUMMARY_MAX, TOPICS_MAX, TOPIC_MAX, OPEN_MAX = 700, 4, 60, 200
_LINE_MAX = 400             # of one thread's line: a subject is the sender's to make long
_ADDR_MAX = 320


class Thread(TypedDict):
    subject: str
    messages: int
    last: str
    gist: str


class Said(TypedDict):
    summary: str
    topics: list[str]
    open: str


def address(raw: object) -> str | None:
    """The sender a request names, as rows go by them, or None if that's no address."""
    if not isinstance(raw, str):
        return None
    addr = raw.strip().lower()
    return addr if "@" in addr and len(addr) <= _ADDR_MAX else None


def threads_of(rows: Sequence[Row]) -> list[Thread]:
    """A sender's mail, newest first, as its threads, the newest first: one with no thread is its own, by its subject."""
    grouped: dict[tuple[str, str], Thread] = {}
    for r in rows:
        key = (r["account"], r["thread_id"] or f'lone:{r["subject"]}')
        held = grouped.get(key)
        if held is None:
            grouped[key] = {"subject": r["subject"] or "(no subject)", "messages": 1, "last": (r["date_iso"] or "")[:10],
                            "gist": r["summary"] or r["snippet"] or ""}
        else:
            held["messages"] += 1
    return list(grouped.values())[:THREADS]


def prompt_lines(threads: Sequence[Thread]) -> str:
    """The threads as the model is told them, within what it may be told."""
    out: list[str] = []
    total = 0
    for t in threads:
        line = f'- {t["subject"]} ({t["messages"]} message{"" if t["messages"] == 1 else "s"}, latest {t["last"]})'
        if t["gist"]:
            line += f': {t["gist"]}'
        line = line[:_LINE_MAX]
        if total + len(line) > CAP_TOTAL:
            break
        total += len(line) + 1
        out.append(line)
    return "\n".join(out)


def clean(obj: Mapping[str, Any]) -> Said:
    told = obj.get("topics")
    topics = [str(t).strip()[:TOPIC_MAX] for t in (told if is_list(told) else []) if isinstance(t, str) and t.strip()]
    return {"summary": str(obj.get("summary") or "").strip()[:SUMMARY_MAX], "topics": topics[:TOPICS_MAX],
            "open": str(obj.get("open") or "").strip()[:OPEN_MAX]}


def fallback(threads: Sequence[Thread]) -> Said:
    """For an answer with nothing in it: what the mail itself says."""
    n = len(threads)
    return {"summary": f'{n} recent thread{"" if n == 1 else "s"}, the latest about {threads[0]["subject"]}.', "topics": [], "open": ""}
