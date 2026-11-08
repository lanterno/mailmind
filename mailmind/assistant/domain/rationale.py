"""Why a sender is worth cleaning up, with no I/O: who may be asked about, what the model is told of each, and what is taken from
its answer. The numbers are the stored mail's, so the model puts them in words and invents none, and a sender it says nothing of
gets the same numbers in plain words."""
from collections.abc import Mapping, Sequence
from typing import Any

from mailmind.kernel.parsed import is_list, is_object

MAX_SENDERS = 40            # how many one pass reads: what the list of noisy senders shows
TEXT_MAX = 200              # of one rationale
_ADDR_MAX = 320


def named(raw: object) -> list[str] | None:
    """The distinct senders a request names, lower-cased as rows go by them, or None if it names none, too many, or anything
    that isn't an address."""
    if not is_list(raw) or not raw or len(raw) > MAX_SENDERS:
        return None
    out: list[str] = []
    for one in raw:
        if not isinstance(one, str) or "@" not in one or len(one) > _ADDR_MAX:
            return None
        addr = one.strip().lower()
        if addr not in out:
            out.append(addr)
    return out


def span(seconds: float) -> str:
    """How long `seconds` is, in the words a sentence would use."""
    days = max(0, int(seconds // 86400))
    if days == 0:
        return "under a day"
    if days < 60:
        return f"{days} day{'' if days == 1 else 's'}"
    if days < 730:
        return f"{days // 30} months"
    return f"{days // 365} years"


def line(n: int, row: Mapping[str, Any], now: float) -> str:
    """One sender's numbers as the model is told them, under number `n`."""
    return (f'[{n}] {row["name"] or row["addr"]} <{row["addr"]}>: {row["n"]} messages over {span(now - row["first_ts"])}, '
            f'{row["unseen"]} of them never opened, {row["replied"]} replied to, the latest {span(now - row["last_ts"])} ago.')


def fallback(row: Mapping[str, Any], now: float) -> str:
    """The same numbers in plain words, for a sender the model said nothing about."""
    return (f'{row["unseen"]} of {row["n"]} unopened over {span(now - row["first_ts"])}, '
            f'{row["replied"]} replied to.')[:TEXT_MAX]


def clean(obj: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], now: float) -> dict[str, str]:
    """One rationale for each sender in `rows`, the first of which was told as number 1: what the model said of it, or else
    `fallback`. A number it wasn't shown says nothing of anyone, and the first word on a sender stands."""
    told = obj.get("rationales")
    said: dict[int, str] = {}
    for item in told if is_list(told) else []:
        if not is_object(item):
            continue
        n, text = item.get("id"), item.get("text")
        if isinstance(n, bool) or not isinstance(n, int) or n in said:
            continue
        if isinstance(text, str) and text.strip():
            said[n] = text.strip()[:TEXT_MAX]
    return {r["addr"]: said.get(n) or fallback(r, now) for n, r in enumerate(rows, 1)}
