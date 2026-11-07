"""A brief and a delta, with no I/O: what is taken from a model's answer, what stands in when the answer won't do, and how far
back a delta may look. A model's answer is its own words, so each is held to its caps here, and a brief's ids to what it
was shown."""
from collections.abc import Collection, Mapping
from typing import Any, TypedDict

from mailmind.assistant.domain import citations
from mailmind.kernel import attention
from mailmind.kernel.parsed import is_object
from mailmind.kernel.row import Row

GROUPS_MAX = 6
DELTA_TOP_N = 25
DELTA_FLOOR = 24 * 3600     # how far back a delta looks at most
DELTA_FLOOR_STEP = 600      # the floor moves in these steps


class Group(TypedDict):
    label: str
    count: int
    pks: list[int]


class Card(TypedDict):
    narrative: str
    groups: list[Group]


class DeltaItem(TypedDict):
    who: str
    gist: str
    importance: int


def fallback(rows: list[Row]) -> Card:
    """Deterministic narrative + groups straight from the rows — used when Claude is flaky,
    so a bad reply is cached once rather than retried forever."""
    by_cat: dict[str, list[int]] = {}
    for r in rows:
        by_cat.setdefault(r["category"] or "other", []).append(r["pk"])
    need = [r["pk"] for r in rows if attention.needs_you(r["importance"], r["category"], r["action"])]
    groups: list[Group] = []
    if need:
        groups.append({"label": "Needs a reply", "count": len(need), "pks": need[:60]})
    for cat, pks in sorted(by_cat.items(), key=lambda kv: -len(kv[1]))[:5]:
        groups.append({"label": cat, "count": len(pks), "pks": pks[:60]})
    narrative = (f"{len(rows)} items need a glance"
                 + (f" — {len(need)} look like they need a reply" if need else "")
                 + f". Grouped into {len(groups)} buckets below.")
    return {"narrative": narrative, "groups": groups}


def trim_narrative(s: str | None, cap: int = 1500) -> str:
    """A 3-5 sentence prose brief is ~500-1200 chars; the old hard [:800] cut it mid-word
    ("…mimicking syste"). Keep it whole under a generous cap; only if a runaway reply
    exceeds it, end on the last complete sentence (never mid-word), then ellipsize."""
    s = (s or "").strip()
    if len(s) <= cap:
        return s
    cut = s[:cap]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end > cap * 0.5:                     # a clean sentence break in the back half → stop there
        return cut[:end + 1].rstrip()
    sp = cut.rfind(" ", int(cap * 0.5))      # a word boundary in the BACK half only (never collapse to a
    return (cut[:sp] if sp > 0 else cut).rstrip() + "…"   # first-half space; else hard-cut at the cap)


def clean(obj: Mapping[str, Any], valid_pks: Collection[int]) -> tuple[str, list[Group]]:
    """Validate/clamp the model output; intersect pks with real ids (never trust invented ids)."""
    narrative = trim_narrative(str(obj.get("narrative") or ""))
    groups: list[Group] = []
    told: list[Any] = obj.get("groups") or []
    for g in told[:GROUPS_MAX]:
        if not is_object(g):
            continue
        named: list[Any] = g.get("pks") or []
        pks = citations.in_group(named, valid_pks)
        if not pks:
            continue
        groups.append({"label": str(g.get("label") or "")[:40], "count": len(pks), "pks": pks})
    return narrative, groups


def clean_delta(obj: Mapping[str, Any]) -> tuple[str, list[DeltaItem]]:
    headline = str(obj.get("headline") or "")[:300]
    items: list[DeltaItem] = []
    told: list[Any] = obj.get("items") or []
    for it in told[:DELTA_TOP_N]:
        if not is_object(it):
            continue
        try:
            imp = int(it.get("importance") or 2)
        except Exception:
            imp = 2
        items.append({"who": str(it.get("who") or "")[:60],
                      "gist": str(it.get("gist") or "")[:120], "importance": imp})
    return headline, items


def delta_fallback(rows: list[Row]) -> tuple[str, list[DeltaItem]]:
    """Deterministic headline + items — caches a usable card once when Claude's reply is unusable."""
    items: list[DeltaItem] = [{"who": (r["from_name"] or r["from_addr"] or "someone")[:60],
                               "gist": (r["subject"] or "(no subject)")[:120], "importance": r["importance"]}
                              for r in rows[:DELTA_TOP_N]]
    return f"{len(rows)} new since you last looked.", items


def delta_since(watermark: int, now: int) -> int:
    """What a delta looks back to: the watermark a tab sent, no further back than a day and no later than now (a tab's
    clock may be ahead). The day's floor moves in 10-minute steps, so a stale watermark gives a stable key and not a fresh
    claude call every second."""
    floor = now - DELTA_FLOOR
    floor -= floor % DELTA_FLOOR_STEP
    return min(max(watermark, floor), now)
