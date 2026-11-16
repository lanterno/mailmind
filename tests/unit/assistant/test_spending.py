"""What AI cost over the last week and this month, by what a person calls each feature: the Spending card's."""
from mailmind.assistant.domain import pricing, spending


def day(on, **features):
    """A day of the usage ledger's report: each feature's (calls, cost in micro-dollars), summed."""
    return {"day": on, "calls": sum(c for c, _ in features.values()), "cost_micros": sum(m for _, m in features.values()),
            "features": {name.replace("_", "-"): {"calls": c, "cost_micros": m} for name, (c, m) in features.items()}}


def test_a_feature_made_two_ways_is_one_line_and_one_not_known_is_other():
    week = spending.period([day("2026-11-02", triage=(3, 300), triage_batch=(10, 500), brief_ahead=(1, 900)),
                            day("2026-11-01", brief=(1, 800), future_feature=(2, 20))])
    assert week == {"calls": 17, "cost_micros": 2520, "features": [
        {"label": "Today's brief", "calls": 2, "cost_micros": 1700},
        {"label": "Summaries", "calls": 13, "cost_micros": 800},
        {"label": "Other", "calls": 2, "cost_micros": 20}]}


def test_a_line_with_no_call_is_left_out_and_lines_that_cost_the_same_go_by_calls_then_name():
    period = spending.period([day("2026-11-02", ask=(1, 100), rewrite=(2, 100), extract=(1, 100), thread=(0, 0))])
    assert [f["label"] for f in period["features"]] == ["Rewrite", "Ask", "Dates and to-dos"]


def test_the_week_is_the_last_seven_days_and_the_month_its_days_so_far():
    days = [day(f"2026-11-{d:02d}", ask=(1, 10)) for d in (3, 2, 1)] + [day(f"2026-10-{d:02d}", ask=(1, 10))
                                                                       for d in range(31, 3, -1)]
    report = spending.report(days)
    assert (report["week"]["calls"], report["month"]["calls"]) == (7, 3)
    assert spending.report([]) == {"week": {"calls": 0, "cost_micros": 0, "features": []},
                                   "month": {"calls": 0, "cost_micros": 0, "features": []}}


def test_a_sum_is_held_at_the_most_the_ledger_holds():
    most = spending.period([day("2026-11-02", ask=(1, pricing.MOST)), day("2026-11-01", ask=(1, pricing.MOST))])
    assert (most["cost_micros"], most["features"][0]["cost_micros"]) == (pricing.MOST, pricing.MOST)


def test_every_feature_a_call_names_has_its_own_words():
    for feature in ("triage", "triage-batch", "thread", "brief", "brief-ahead", "delta", "review", "review-ahead", "draft",
                    "draft-ahead", "rewrite", "extract", "screen", "rationale", "sender", "ask", "plan"):
        assert spending.label(feature) != spending.OTHER, feature
