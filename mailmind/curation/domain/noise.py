"""How much of a nuisance a sender is, for ranking who to clean up first."""
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from mailmind.curation.domain.classifier import MKT, NOTIF
from mailmind.curation.domain.rulebook import domain_of
from mailmind.kernel.row import Row

BUCKETS = (MKT, NOTIF)      # whose mail is worth asking about, unless a request names others
MIN_COUNT = 3
LIMIT = 40
MAX_LIMIT = 200
MIN_SCORE = 1.5


def noise_score(count: int, unseen: int, age_days: float) -> float:
    """More mail, more of it unread and more recent all score higher."""
    ratio = (unseen / count) if count else 0
    return count * (0.3 + 0.7 * ratio) * math.exp(-age_days / 30.0)


@dataclass(frozen=True)
class Asked:
    """Whose mail a request asks about, how many messages make a candidate and how many to list."""
    buckets: list[str]
    min_count: int
    limit: int


def _number(qs: Mapping[str, Sequence[str]], name: str, default: int) -> int | None:
    try:
        return int((qs.get(name) or [default])[0])
    except Exception:
        return None


def asked(qs: Mapping[str, Sequence[str]]) -> Asked:
    """What a query string asks for. A number that won't read goes back to the default, and one out of range to the range."""
    min_count, limit = _number(qs, "min_count", MIN_COUNT), _number(qs, "limit", LIMIT)
    return Asked([b for b in ((qs.get("buckets") or [",".join(BUCKETS)])[0]).split(",") if b],
                 MIN_COUNT if min_count is None else max(1, min_count),
                 LIMIT if limit is None else max(1, min(MAX_LIMIT, limit)))


def age_in_days(last_ts: float | None, now: float) -> float:
    """How long since a sender last wrote. With no date, or one ahead of now, it counts as today."""
    return max(0.0, (now - (last_ts or now)) / 86400.0)


@dataclass(frozen=True)
class Candidate:
    """A sender worth cleaning up after, as the numbers in its row and the score they come to."""
    addr: str
    name: str
    domain: str
    count: int
    unseen: int
    unseen_ratio: float
    last_iso: str | None
    bucket: str
    list_likely: bool       # a newsletter most likely offers a way out, before anyone has looked
    score: float


def _candidate(row: Row, now: float) -> Candidate | None:
    n = row["n"] or 0
    unseen = row["unseen"] or 0
    score = noise_score(n, unseen, age_in_days(row["last_ts"], now))
    if score < MIN_SCORE:
        return None
    addr = row["addr"]
    return Candidate(addr, row["name"] or addr, domain_of(addr), n, unseen, round((unseen / n) if n else 0, 2), row["last_iso"],
                     row["bucket"], row["bucket"] == MKT, round(score, 2))


def ranked(rows: Iterable[Row], now: float, limit: int) -> list[Candidate]:
    """The noisiest of `rows`, one a sender, down to MIN_SCORE, by the score as shown, and those it ties with in the order
    they came. A score just under MIN_SCORE stays out, though it would round up to it."""
    candidates = [c for c in (_candidate(row, now) for row in rows) if c is not None]
    candidates.sort(key=lambda c: -c.score)
    return candidates[:limit]
