"""Who is worth cleaning up after, in what order, with no database and no clock."""
import math

import pytest

from mailmind.curation.domain import noise
from mailmind.curation.domain.classifier import MKT, NOTIF

NOW = 1_800_000_000.0
DAY = 86400.0


def row(addr="a@news.example.test", n=10, unseen=0, last_ts=NOW, bucket=MKT, name="News", last_iso="2026-11-01T00:00:00+00:00"):
    return {"addr": addr, "name": name, "n": n, "unseen": unseen, "last_ts": last_ts, "last_iso": last_iso, "bucket": bucket}


# ── the score ─────────────────────────────────────────────────────────────────
def test_more_mail_more_of_it_unread_and_more_recent_all_score_higher():
    base = noise.noise_score(10, 2, 5)
    assert noise.noise_score(20, 4, 5) > base
    assert noise.noise_score(10, 8, 5) > base
    assert noise.noise_score(10, 2, 1) > base


def test_the_score_is_the_count_weighted_by_unread_and_fading_over_a_month():
    assert noise.noise_score(10, 0, 0) == pytest.approx(3.0)
    assert noise.noise_score(10, 10, 0) == pytest.approx(10.0)
    assert noise.noise_score(10, 0, 30) == pytest.approx(3.0 / math.e)
    assert noise.noise_score(0, 0, 0) == 0


# ── its age ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("last_ts, days", [(NOW - 3 * DAY, 3.0), (NOW, 0.0), (None, 0.0), (0, 0.0), (NOW + DAY, 0.0)])
def test_with_no_date_or_one_ahead_of_now_it_counts_as_today(last_ts, days):
    assert noise.age_in_days(last_ts, NOW) == days


# ── who it asks about ─────────────────────────────────────────────────────────
def test_it_is_newsletters_and_notifications_three_messages_and_forty_senders():
    assert (noise.BUCKETS, noise.MIN_COUNT, noise.LIMIT) == ((MKT, NOTIF), 3, 40)


# ── who makes the list ────────────────────────────────────────────────────────
def test_a_candidate_is_its_rows_numbers_and_what_they_come_to():
    [got] = noise.ranked([row(n=8, unseen=2, name="News", last_iso="2026-10-31T00:00:00+00:00")], NOW, 40)
    assert got == noise.Candidate("a@news.example.test", "News", "news.example.test", 8, 2, 0.25, "2026-10-31T00:00:00+00:00", MKT, True,
                                  round(8 * (0.3 + 0.7 * 0.25), 2))


def test_a_sender_with_no_name_goes_by_address():
    assert noise.ranked([row(name=None)], NOW, 40)[0].name == "a@news.example.test"


def test_only_a_newsletter_is_likely_to_offer_a_way_out():
    assert noise.ranked([row(bucket=MKT)], NOW, 40)[0].list_likely is True
    assert noise.ranked([row(bucket=NOTIF)], NOW, 40)[0].list_likely is False


def test_nothing_filled_in_scores_nothing():
    assert noise.ranked([row(n=None, unseen=None)], NOW, 40) == []


def test_a_row_with_no_unread_count_has_none_unread():
    assert noise.ranked([row(unseen=None)], NOW, 40)[0].unseen == 0


def test_the_score_is_read_at_its_age_not_as_if_it_wrote_today():
    assert noise.ranked([row(n=20, last_ts=NOW - 30 * DAY)], NOW, 40)[0].score == round(6.0 / math.e, 2)


def test_the_ratio_and_the_score_are_shown_to_two_places():
    [got] = noise.ranked([row(n=7, unseen=2)], NOW, 40)
    assert got.unseen_ratio == 0.29 and got.score == round(7 * (0.3 + 0.7 * (2 / 7)), 2)


def test_a_score_at_the_bar_makes_it_and_one_just_under_does_not_though_it_would_round_up():
    assert [c.score for c in noise.ranked([row(n=5)], NOW, 40)] == [1.5]
    assert noise.ranked([row(n=5, last_ts=NOW - DAY / 100)], NOW, 40) == []


# ── the order ─────────────────────────────────────────────────────────────────
def test_the_noisiest_come_first_and_the_limit_cuts_from_the_bottom():
    rows = [row("a@example.test", n=5), row("b@example.test", n=20), row("c@example.test", n=10)]
    assert [c.addr for c in noise.ranked(rows, NOW, 40)] == ["b@example.test", "c@example.test", "a@example.test"]
    assert [c.addr for c in noise.ranked(rows, NOW, 2)] == ["b@example.test", "c@example.test"]


def test_scores_that_show_the_same_stay_in_the_order_they_came():
    rows = [row("older@example.test", last_ts=NOW - DAY / 1000), row("newer@example.test")]     # 2.9999 and 3.0 both show as 3.0
    assert [c.addr for c in noise.ranked(rows, NOW, 40)] == ["older@example.test", "newer@example.test"]
