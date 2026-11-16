"""What AI cost over the last week and this month, by what a person would call each feature: what the Spending card in
Settings shows. The provider's own console holds the bill; this is what MailMind counted from each call it made.

A feature's calls are counted under the name the call gave the usage ledger; the ones a person sees as one thing (a
summary made one at a time or in a batch, a brief asked for or written ahead) are summed under one label. A name not
listed here is "Other".
"""
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from mailmind.assistant.domain import pricing

WEEK_DAYS = 7
MONTH_DAYS = 31                     # the most days a month has, so the usage asked for always holds this month

_LABELS = {
    "triage": "Summaries", "triage-batch": "Summaries",
    "thread": "Conversation summaries",
    "brief": "Today's brief", "brief-ahead": "Today's brief",
    "delta": "What came in since you looked",
    "review": "Your week", "review-ahead": "Your week",
    "draft": "Drafted replies",
    "draft-ahead": "Replies drafted ahead",
    "rewrite": "Rewrite",
    "extract": "Dates and to-dos",
    "screen": "The Screener",
    "rationale": "Cleanup suggestions",
    "sender": "About a sender",
    "ask": "Ask",
    "plan": "Commands in words",
}
OTHER = "Other"


def label(feature: str) -> str:
    """What a person would call the feature the usage ledger names `feature`."""
    return _LABELS.get(feature, OTHER)


def period(days: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """`days` (the usage ledger's report, `Gateway.usage`) summed: the calls and their cost in micro-dollars, in all and by
    label, the dearest first (then the most calls, then by label), with no label that made no call. Each sum is held at
    the most the ledger holds."""
    calls = cost = 0
    by_label: dict[str, list[int]] = {}
    for day in days:
        calls = min(calls + day["calls"], pricing.MOST)
        cost = min(cost + day["cost_micros"], pricing.MOST)
        for feature, spent in day["features"].items():
            summed = by_label.setdefault(label(feature), [0, 0])
            summed[0] = min(summed[0] + spent["calls"], pricing.MOST)
            summed[1] = min(summed[1] + spent["cost_micros"], pricing.MOST)
    kept = sorted(((name, c, m) for name, (c, m) in by_label.items() if c > 0 or m > 0), key=lambda f: (-f[2], -f[1], f[0]))
    features = [{"label": name, "calls": c, "cost_micros": m} for name, c, m in kept]
    return {"calls": calls, "cost_micros": cost, "features": features}


def report(days: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The last week and this month, from `days`, the usage ledger's report of at least MONTH_DAYS local days, newest
    first: the week is the last WEEK_DAYS days, today among them, and the month the days of today's month so far."""
    month = days[0]["day"][:7] if days else ""
    return {"week": period(days[:WEEK_DAYS]), "month": period(d for d in days if d["day"][:7] == month)}
