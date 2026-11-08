"""A pass over the senders waiting in the Screener, with no I/O: who a pass may name, what the model is told of each, and what
is taken from its answer. The answer is its own words on a stranger's mail, so a verdict names a sender only by a number it was
shown, and only one said surely enough stands."""
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict, get_args

from mailmind.kernel.parsed import is_list, is_object
from mailmind.kernel.row import Row
from mailmind.kernel.sender_verdict import Verdict

MAX_SENDERS = 40            # how many senders one pass reads: the Screener's newest
SUBJECTS = 3                # how many of a sender's newest messages it is told of
MIN_CONFIDENCE = 0.8        # below this, nothing is said: a hunch on a row someone will act on is worse than none
WHY_MAX = 120
_LINE_MAX = 400             # of one sender's line: a subject is the sender's to make long
_GIST_MAX = 80              # of what the AI said of a message
_VERDICTS: tuple[Verdict, ...] = get_args(Verdict.__value__)


class Suggested(TypedDict):
    from_addr: str
    verdict: Verdict
    confidence: float
    why: str


def line(n: int, sender: Mapping[str, Any], recent: Sequence[Row]) -> str:
    """One sender as the model is told of them, under number `n`."""
    seen = "; ".join(r["subject"] or "(no subject)" for r in recent)
    gist = next((r["summary"][:_GIST_MAX] for r in recent if r["summary"]), "")
    said = f" AI on the latest: {gist}" if gist else ""
    return f'[{n}] {sender["from_name"]} <{sender["from_addr"]}>, {sender["count"]} in the inbox. Subjects: {seen}.{said}'[:_LINE_MAX]


def _confidence(told: object) -> float:
    """How sure the model said it was, from 0 to 1. What can't be read as that isn't sure at all."""
    if isinstance(told, bool) or not isinstance(told, (int, float)) or told != told:      # NaN equals nothing
        return 0.0
    return max(0.0, min(1.0, float(told)))


def clean(obj: Mapping[str, Any], shown: Sequence[str]) -> list[Suggested]:
    """What stands of an answer about the senders in `shown`, the first of which was told as number 1. One verdict a sender:
    the first it gave. A number it wasn't shown, a verdict that is no rule's and anything said less surely than MIN_CONFIDENCE
    all go."""
    told = obj.get("verdicts")
    out: list[Suggested] = []
    taken: set[int] = set()
    for item in told if is_list(told) else []:
        if not is_object(item):
            continue
        n, verdict, sure = item.get("id"), item.get("verdict"), _confidence(item.get("confidence"))
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= len(shown) or n in taken:
            continue
        if verdict not in _VERDICTS or sure < MIN_CONFIDENCE:
            continue
        taken.add(n)
        out.append({"from_addr": shown[n - 1], "verdict": verdict, "confidence": round(sure, 2), "why": str(item.get("why") or "")[:WHY_MAX]})
    return out
