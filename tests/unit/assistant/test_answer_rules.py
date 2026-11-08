"""What is taken from a model's answer for a brief, a delta, a weekly review and a thread summary, and what stands in when
the answer won't do, with no I/O. A model's answer is its own words, so each is held to its caps."""
import json

import pytest

from mailmind.assistant.domain import brief, digest, review
from mailmind.assistant.domain.schema import object_in


def row(pk, category="info", importance=2, action="", **over):
    return {"pk": pk, "category": category, "importance": importance, "action": action, "from_name": "Bea",
            "from_addr": "bea@example.test", "subject": f"Subject {pk}", **over}


# ── a brief ───────────────────────────────────────────────────────────────────

def test_a_brief_keeps_only_the_ids_it_was_shown():
    narrative, groups = brief.clean({"narrative": " Quiet day. ", "groups": [{"label": "Bills", "pks": [1, 999, 2]}]}, {1, 2})
    assert narrative == "Quiet day."
    assert groups == [{"label": "Bills", "count": 2, "pks": [1, 2]}]


def test_a_group_with_nothing_real_in_it_goes():
    assert brief.clean({"groups": [{"label": "Made up", "pks": [999]}, {"label": "None", "pks": []}, "junk"]}, {1})[1] == []


def test_a_brief_holds_its_groups_to_six_and_a_label_to_its_cap():
    told = [{"label": "L" * 60, "pks": [1]} for _ in range(9)]
    _, groups = brief.clean({"groups": told}, {1})
    assert len(groups) == brief.GROUPS_MAX and groups[0]["label"] == "L" * 40


def test_a_brief_that_says_nothing_has_no_narrative():
    assert brief.clean({}, {1}) == ("", [])


def test_a_short_narrative_is_kept_whole():
    assert brief.trim_narrative("One sentence.") == "One sentence."
    assert brief.trim_narrative(None) == ""


def test_a_runaway_narrative_ends_on_its_last_sentence_in_the_back_half():
    s = "A" * 1200 + ". " + "b " * 400
    assert brief.trim_narrative(s) == "A" * 1200 + "."


def test_a_runaway_narrative_with_no_sentence_ends_on_a_word_and_says_so():
    s = "A" * 900 + " " + "b" * 900
    assert brief.trim_narrative(s) == "A" * 900 + "…"


def test_a_runaway_narrative_with_no_break_is_cut_at_the_cap():
    assert brief.trim_narrative("A" * 2000) == "A" * 1500 + "…"


def test_a_brief_falls_back_on_the_rows_themselves():
    card = brief.fallback([row(1, "action-needed", 5, "Reply"), row(2), row(3)])
    assert card["narrative"] == "3 items need a glance — 1 look like they need a reply. Grouped into 3 buckets below."
    assert card["groups"] == [{"label": "Needs a reply", "count": 1, "pks": [1]},
                              {"label": "info", "count": 2, "pks": [2, 3]},
                              {"label": "action-needed", "count": 1, "pks": [1]}]


def test_a_quiet_brief_falls_back_without_the_reply_clause_and_a_row_with_no_category_is_other():
    card = brief.fallback([row(1, None)])
    assert card["narrative"] == "1 items need a glance. Grouped into 1 buckets below."
    assert card["groups"] == [{"label": "other", "count": 1, "pks": [1]}]


# ── a delta ───────────────────────────────────────────────────────────────────

def test_a_delta_holds_each_part_to_its_cap():
    headline, items = brief.clean_delta({"headline": "H" * 400, "items": [{"who": "W" * 80, "gist": "G" * 200, "importance": 4}]})
    assert headline == "H" * 300
    assert items == [{"who": "W" * 60, "gist": "G" * 120, "importance": 4}]


def test_a_delta_reads_an_odd_importance_as_two_and_passes_over_what_is_no_item():
    _, items = brief.clean_delta({"items": ["junk", {"importance": "soon"}, {}]})
    assert [i["importance"] for i in items] == [2, 2]


def test_a_delta_holds_its_items_to_its_top():
    assert len(brief.clean_delta({"items": [{}] * 40})[1]) == brief.DELTA_TOP_N


def test_a_delta_falls_back_on_the_rows_themselves():
    headline, items = brief.delta_fallback([row(1, subject=None, from_name=None)])
    assert headline == "1 new since you last looked."
    assert items == [{"who": "bea@example.test", "gist": "(no subject)", "importance": 2}]


NOW = 1_800_000_123


def test_a_delta_looks_back_to_the_watermark():
    assert brief.delta_since(NOW - 3600, NOW) == NOW - 3600


def test_a_watermark_from_ahead_of_us_is_now():
    assert brief.delta_since(NOW + 500, NOW) == NOW


def test_a_stale_watermark_stops_at_a_day_ago_on_a_ten_minute_step():
    floor = brief.delta_since(0, NOW)
    assert floor % brief.DELTA_FLOOR_STEP == 0
    assert NOW - brief.DELTA_FLOOR - brief.DELTA_FLOOR_STEP < floor <= NOW - brief.DELTA_FLOOR


def test_two_stale_watermarks_a_moment_apart_look_back_to_the_same_place():
    start = NOW - NOW % brief.DELTA_FLOOR_STEP
    assert brief.delta_since(0, start) == brief.delta_since(5, start + 599)


# ── a weekly review ───────────────────────────────────────────────────────────

STATS = {"sent": 1, "received": 2, "by_bucket": {}, "needs_you": 1, "waiting_on": 0, "reply_later": 0, "cleaned": 0, "handled": 0}
NEEDED = {"account": "ada@example.test", "thread_id": "t1", "who": "Bea", "subject": "Lunch"}


def test_a_review_holds_its_narrative_and_its_next_steps_to_their_caps():
    got = review.clean({"narrative": "N" * 2000, "next": ["x" * 200, 7, "b", "c", "d"]})
    assert got == {"narrative": "N" * 1400, "next": ["x" * 160, "b", "c"]}


def test_a_stub_is_no_retrospective():
    assert review.degenerate("")
    assert review.degenerate("test")
    assert review.degenerate("x" * 80)                                          # long, but one word
    assert review.degenerate("A few words only here now ok fine")               # eight words, under forty characters
    assert not review.degenerate("You sent a few and a few more came in over the week.")


def test_a_review_falls_back_on_the_counts():
    got = review.fallback(STATS, [NEEDED], [])
    assert got == {"narrative": "In the last 7 days you sent 1 message and 2 came in. 1 still need a reply from you.",
                   "next": ["Reply to Bea — Lunch"]}


def test_a_clear_week_says_so():
    got = review.fallback({**STATS, "sent": 2, "needs_you": 0}, [], [])
    assert got["narrative"] == "In the last 7 days you sent 2 messages and 2 came in. Your inbox is clear — nothing is waiting on you."


def test_a_week_with_someone_to_hear_from_says_so():
    got = review.fallback({**STATS, "waiting_on": 3}, [], [])
    assert got["narrative"].endswith("1 still need a reply from you and you're waiting to hear back on 3.")


def test_a_reviews_fingerprint_moves_with_its_counts_and_its_threads_and_nothing_else():
    fp = review.fingerprint_of(STATS, [NEEDED], [])
    assert fp == review.fingerprint_of(STATS, [{**NEEDED, "pk": 9, "subject": "Renamed"}], [])
    assert fp != review.fingerprint_of({**STATS, "sent": 9}, [NEEDED], [])
    assert fp != review.fingerprint_of({**STATS, "handled": 9}, [NEEDED], [])
    assert fp != review.fingerprint_of(STATS, [{**NEEDED, "thread_id": "t2"}], [])
    assert fp != review.fingerprint_of(STATS, [], [NEEDED])


# ── a thread summary ──────────────────────────────────────────────────────────

def test_a_summary_is_read_out_of_its_reply_and_held_to_its_caps():
    reply = "Here it is: " + json.dumps({"tldr": "T" * 700, "points": ["p" * 300] * 8, "action": "a" * 300,
                                         "participants": ["w" * 100] * 14})
    got = digest.parse(reply)
    assert got == {"tldr": "T" * 600, "points": ["p" * 200] * 6, "action": "a" * 200, "participants": ["w" * 80] * 12}


@pytest.mark.parametrize("out", [None, "", "no json here", "{broken"])
def test_a_reply_with_no_json_has_no_summary(out):
    assert digest.parse(out) is None


def test_parts_that_are_no_list_read_as_none():
    assert digest.parse('{"tldr": "T", "points": "one", "participants": 3}') == {"tldr": "T", "points": [], "action": "", "participants": []}


# ── the object in a reply ─────────────────────────────────────────────────────

def test_the_object_a_reply_holds():
    assert object_in('{"a": 1}') == {"a": 1}
    assert object_in('Sure: {"a": 1} there') == {"a": 1}


@pytest.mark.parametrize("out", ["", "no json", "{broken", "[1, 2]", "3"])
def test_a_reply_holding_no_object_gives_an_empty_one(out):
    assert object_in(out) == {}
