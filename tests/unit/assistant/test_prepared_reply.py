"""The rules of a reply made ready ahead of time, with no I/O: which one it is, which mail may have one, when a stored one
may be served, and how drafting ahead backs off. Rows are plain dicts, as a stored row reads."""
import pytest

from mailmind.assistant.domain import prepared_reply as pr
from mailmind.assistant.domain.prepared_reply import Breaker

ADA = "ada@example.test"
NOW = 1_800_000_000


def message(**over):
    return {"account": ADA, "thread_id": "t-lunch", "message_id": "<m1@example.test>", "is_junk": 0, "answered": 0,
            "snooze_until": None, "from_addr": "bea@example.test", "folder": "INBOX", **over}


def stored(**over):
    return {"body": "Thursday works.", "fp": "t2:2:200:abc", "created_ts": NOW - 60, "fail_ts": 0, "fail_count": 0, **over}


def everyone(local):
    return True


# ── which one it is ───────────────────────────────────────────────────────────

def test_a_threaded_message_goes_by_its_thread():
    assert pr.key_of(message()) == (ADA, "t:t-lunch")


def test_a_message_with_no_thread_goes_by_its_message_id():
    assert pr.key_of(message(thread_id=None)) == (ADA, "m:<m1@example.test>")
    assert pr.key_of(message(thread_id="   ")) == (ADA, "m:<m1@example.test>")


def test_a_message_with_neither_has_no_key():
    assert pr.key_of(message(thread_id="", message_id="  ")) is None
    assert pr.key_of(message(thread_id=None, message_id=None)) is None


# ── which mail may have one ───────────────────────────────────────────────────

def test_mail_from_someone_may_have_one():
    assert pr.eligible(message(), everyone)


@pytest.mark.parametrize("over", [
    {"is_junk": 1},
    {"answered": 1},
    {"snooze_until": 1_800_000_500},
    {"from_addr": ADA},
    {"from_addr": ADA.upper()},
    {"folder": "Sent Mail"},
    {"from_addr": None},
    {"from_addr": ""},
])
def test_mail_already_handled_or_of_your_own_may_not(over):
    assert not pr.eligible(message(**over), everyone)


def test_no_row_may_not():
    assert not pr.eligible(None, everyone)


def test_curation_has_the_word_on_a_sender_and_is_asked_in_lower_case():
    asked = []

    def looks_human(local):
        asked.append(local)
        return local != "no-reply"
    assert pr.eligible(message(from_addr="Bea@example.test"), looks_human)
    assert not pr.eligible(message(from_addr="no-reply@example.test"), looks_human)
    assert asked == ["bea", "no-reply"]


# ── when a stored one may be served ───────────────────────────────────────────

def test_a_young_draft_for_the_thread_as_it_is_may_be_served():
    assert pr.servable(stored(), "t2:2:200:abc", NOW)


@pytest.mark.parametrize("row, fp", [
    (None, "t2:2:200:abc"),
    (stored(body=None), "t2:2:200:abc"),
    (stored(body=""), "t2:2:200:abc"),
    (stored(), "t2:3:300:def"),                     # the thread has moved on
])
def test_one_with_nothing_to_say_or_for_another_state_of_the_thread_may_not(row, fp):
    assert not pr.servable(row, fp, NOW)


def test_a_draft_ages_out_at_twenty_four_hours():
    assert pr.servable(stored(created_ts=NOW - pr.TTL + 1), "t2:2:200:abc", NOW)
    assert not pr.servable(stored(created_ts=NOW - pr.TTL), "t2:2:200:abc", NOW)
    assert pr.TTL == 24 * 3600


def test_a_thread_has_one_for_its_anchor_only():
    assert pr.for_the_anchor(7, "t-lunch", 7)
    assert not pr.for_the_anchor(6, "t-lunch", 7)
    assert not pr.for_the_anchor(6, "t-lunch", None)


def test_a_message_with_no_thread_is_its_own_anchor():
    assert pr.for_the_anchor(6, None, None)


# ── how it backs off ──────────────────────────────────────────────────────────

def test_opening_a_message_waits_a_minute_after_a_failure():
    assert not pr.failed_lately(None, NOW)
    assert not pr.failed_lately(stored(fail_ts=0), NOW)
    assert pr.failed_lately(stored(fail_ts=NOW - pr.FAIL_TTL + 1), NOW)
    assert not pr.failed_lately(stored(fail_ts=NOW - pr.FAIL_TTL), NOW)


@pytest.mark.parametrize("fails, wait", [(1, 300), (2, 600), (3, 1200), (5, 4800), (7, 19200), (8, 6 * 3600), (40, 6 * 3600)])
def test_the_round_waits_five_minutes_doubling_to_six_hours(fails, wait):
    assert pr.backing_off(stored(fail_ts=NOW - wait + 1, fail_count=fails), NOW)
    assert not pr.backing_off(stored(fail_ts=NOW - wait, fail_count=fails), NOW)


def test_no_failure_is_no_backoff():
    assert not pr.backing_off(None, NOW)
    assert not pr.backing_off(stored(fail_ts=0, fail_count=3), NOW)


def test_only_a_request_of_the_default_shape_is_one_a_prepared_reply_can_answer():
    assert pr.default_shaped("", "balanced")
    assert pr.default_shaped("   ", "balanced")
    assert not pr.default_shaped("say no", "balanced")
    assert not pr.default_shaped("", "warm")


# ── the three strikes ─────────────────────────────────────────────────────────

def test_the_third_failure_in_a_row_opens_the_breaker_for_half_an_hour():
    b = Breaker()
    assert [b.heard(False, NOW), b.heard(False, NOW), b.heard(False, NOW)] == [False, False, True]
    assert b.is_open(NOW) and b.is_open(NOW + pr.SUSPEND - 1)
    assert not b.is_open(NOW + pr.SUSPEND)


def test_a_success_starts_the_count_again():
    b = Breaker()
    for outcome in (False, False, True, False, False):
        assert not b.heard(outcome, NOW)
    assert not b.is_open(NOW)


def test_a_neutral_outcome_neither_trips_nor_resets_the_count():
    b = Breaker()
    assert [b.heard(o, NOW) for o in (False, None, False, None, False)] == [False, False, False, False, True]


def test_the_count_starts_again_once_it_has_tripped():
    b = Breaker()
    for _ in range(3):
        b.heard(False, NOW)
    assert not b.heard(False, NOW + pr.SUSPEND)
    assert not b.is_open(NOW + pr.SUSPEND)
