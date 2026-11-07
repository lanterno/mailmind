"""What a draft still asks of its server, with no database, clock or server: when a push is due, what a save records, what
the journal's row says."""
import hashlib
import json

import pytest

from mailmind.compose.domain import draft_journal as journal
from mailmind.compose.domain.draft_journal import BACKOFF_FIRST, BACKOFF_MOST, PUSH_EVERY, PUSH_TICK, WAITING_AFTER


def spec(**kw):
    return {"account": "ada@example.test", "account_name": "Ada", "to": [], "cc": [], "bcc": [], "subject": "", "text": "",
            "html": None, "attachments": [], "in_reply_to": None, "references": None, "own": "typed",
            "tail": {"text": None, "html": None}, "forwarded": [], **kw}


BUILT = {"raw": b"the bytes", "message_id": "<n@example.test>", "envelope": [], "to_disp": [], "cc_disp": [], "bcc_disp": []}


# -- when a push is due ---------------------------------------------------------

def test_a_first_autosave_waits_a_whole_minute():
    assert journal.due_after_typing(1000, None) == 1000 + PUSH_EVERY


def test_a_later_one_waits_out_the_minute_since_the_last_push():
    assert journal.due_after_typing(1000, 980) == 980 + PUSH_EVERY


def test_a_push_long_ago_leaves_nothing_to_wait_for():
    assert journal.due_after_typing(1000, 100) == 1000


def test_an_autosave_keeps_the_push_already_waiting_and_what_it_has_tried():
    waiting = {"due_at": 1007, "attempts": 3}
    assert journal.after_save(True, 1000, waiting, 980) == (1007, 3)


def test_an_autosave_with_nothing_waiting_goes_by_the_last_push():
    assert journal.after_save(True, 1000, None, 980) == (980 + PUSH_EVERY, 0)
    assert journal.after_save(True, 1000, None, None) == (1000 + PUSH_EVERY, 0)


def test_any_other_save_goes_now_and_afresh():
    assert journal.after_save(False, 1000, {"due_at": 1040, "attempts": 3}, 980) == (1000, 0)


def test_a_push_that_fails_goes_again_later_and_later_up_to_a_limit():
    waits = [journal.due_after_failing(1000, n) - 1000 for n in range(1, 8)]
    assert waits == [BACKOFF_FIRST, 40, 80, 160, 320, 640, BACKOFF_MOST]


def test_the_wait_stays_at_the_limit_however_many_tries_it_has_had():
    assert journal.due_after_failing(1000, 10_000) == 1000 + BACKOFF_MOST


@pytest.mark.parametrize("due, wait", [
    (None, PUSH_TICK),                  # nothing waits: the job still looks
    (1000, PUSH_TICK),                  # due now, on a busy gate
    (900, PUSH_TICK),
    (1003, 3),
    (1000 + PUSH_TICK, PUSH_TICK),
    (1000 + PUSH_TICK + 5, PUSH_TICK),
])
def test_the_job_looks_when_the_next_comes_due_and_at_least_every_tick(due, wait):
    assert journal.next_round_in(1000, due) == wait


def test_the_tray_names_a_save_past_its_grace_and_read_only_every_one():
    assert journal.overdue_since(1000, pushing=True) == 1000 - WAITING_AFTER
    assert journal.overdue_since(1000, pushing=False) == 1000


# -- what a save records --------------------------------------------------------

def test_the_record_describes_these_very_bytes():
    assert journal.record_of(spec(), BUILT)["raw_sha"] == hashlib.sha256(b"the bytes").hexdigest() == journal.sha_of(b"the bytes")


def test_the_text_is_kept_with_lf_line_ends():
    record = journal.record_of(spec(own="one\r\ntwo", tail={"text": "\r\n\r\n> q\r\n", "html": "<blockquote>q</blockquote>"}), BUILT)
    assert (record["own_text"], record["tail_text"], record["tail_html"]) == ("one\ntwo", "\n\n> q\n", "<blockquote>q</blockquote>")


def test_no_quote_is_none_rather_than_empty():
    assert journal.record_of(spec(tail={"text": "", "html": None}), BUILT)["tail_text"] is None


def test_bcc_is_kept_here_as_the_composer_holds_it_since_the_bytes_never_say():
    record = journal.record_of(spec(bcc=["Lin Ma <lin@example.test>", "bea@example.test"]), BUILT)
    assert json.loads(record["bcc"]) == ["Lin Ma <lin@example.test>", "bea@example.test"]


def test_no_bcc_is_none():
    assert journal.record_of(spec(), BUILT)["bcc"] is None


# -- what the row says ----------------------------------------------------------

def test_a_row_asking_only_that_the_copy_goes():
    assert journal.asks_removal({"discard": 1}) is True
    assert journal.asks_removal({"discard": 0}) is False
    assert journal.asks_removal(None) is False


def test_a_row_holding_a_save():
    assert journal.holds_a_save({"raw": b"x"}) is True
    assert journal.holds_a_save({"raw": None}) is False
    assert journal.holds_a_save(None) is False


@pytest.mark.parametrize("row, j, there", [
    ({"uid": 9}, None, True),
    ({"uid": None}, None, False),
    ({"uid": None}, {"tried": None}, False),
    ({"uid": None}, {"tried": "<t@example.test>"}, True),     # a try may have left one
])
def test_a_discard_waits_only_on_a_copy_that_may_be_there(row, j, there):
    assert journal.copy_may_be_there(row, j) is there


def test_a_send_takes_a_copy_a_try_left_in_its_own_account_only():
    j = {"tried": "<t@example.test>", "account": "ada@example.test"}
    assert journal.left_by_a_try(j, "ada@example.test") == "<t@example.test>"
    assert journal.left_by_a_try(j, "bea@example.test") is None
    assert journal.left_by_a_try({**j, "tried": None}, "ada@example.test") is None
    assert journal.left_by_a_try(None, "ada@example.test") is None


def test_the_tray_says_what_waits_with_no_subject_as_empty():
    assert journal.listed({"draft_id": 4, "account": "ada@example.test", "subject": None, "saved_at": 1000, "attempts": 2, "discard": 1}) == {
        "id": 4, "account": "ada@example.test", "subject": "", "saved_at": 1000, "attempts": 2, "discarding": True}
