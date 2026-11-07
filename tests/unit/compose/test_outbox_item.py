"""What a try leaves on an outbox row, what a delivered send owes and how a row reads, with no database, clock or server."""
import pytest

from mailmind.compose.domain import outbox_item
from mailmind.compose.domain.outbox_item import owed


def row(**kw):
    return {"state": "queued", "wire_begun": 0, "delivered": 0, "sent_appended": 0, "answered_set": 0, "followup_recorded": 0,
            "draft_id": None, "draft_deleted": 0, "orig_message_id": None, "in_reply_to_pk": None, "message_id": "<a@example.test>",
            "attempts": 0, **kw}


def listed(**kw):
    return outbox_item.listed(row(**{"id": 1, "account": "ada@example.test", "subject": "Thread colours", "recipients": "{}",
                                     "last_error": None, "created_at": "2026-11-02T12:00:00+00:00", "sent_at": None,
                                     "next_attempt_at": 0, "wait_kind": outbox_item.GRACE, **kw}))


# -- a try that didn't deliver --------------------------------------------------

def test_a_send_no_one_answered_stays_marked_and_goes_to_a_person():
    tried = outbox_item.failed_try(unanswered=True, permanent=False, error="connection lost after the message went out")
    assert (tried.give_up, tried.unsure, tried.error) == (True, True, outbox_item.UNSURE)
    assert tried.marks == {}


def test_unanswered_says_so_whatever_the_error_was_called():
    assert outbox_item.failed_try(unanswered=True, permanent=True, error="rejected").error == outbox_item.UNSURE


def test_a_refusal_for_good_gives_up_with_its_own_words_and_the_mark_gone():
    tried = outbox_item.failed_try(unanswered=False, permanent=True, error="recipient refused")
    assert (tried.give_up, tried.unsure, tried.error) == (True, False, "recipient refused")
    assert tried.marks == {"wire_begun": 0, "wait_kind": outbox_item.BACKOFF}


def test_any_other_failure_backs_off():
    tried = outbox_item.failed_try(unanswered=False, permanent=False, error="connection lost")
    assert (tried.give_up, tried.unsure, tried.error) == (False, False, "connection lost")
    assert tried.marks == {"wire_begun": 0, "wait_kind": outbox_item.BACKOFF}


@pytest.mark.parametrize("codes, said", [
    ([550], True), ([500], True), ([599], True),
    ([450], False), ([600], False), ([499], False),
    ([550, 552], True), ([550, 450], False),            # one recipient may still pass
    ([], False),                                        # no answer says nothing
    (["550"], False), ([None], False),
])
def test_only_every_code_a_5xx_refuses_for_good(codes, said):
    assert outbox_item.refused_for_good(codes) is said


def test_refused_for_good_takes_what_a_generator_gives():
    assert outbox_item.refused_for_good(c for c in (550, 551)) is True
    assert outbox_item.refused_for_good(c for c in ()) is False


# -- unsure ---------------------------------------------------------------------

@pytest.mark.parametrize("kw, said", [
    ({"state": "error", "wire_begun": 1}, True),
    ({"state": "error", "wire_begun": 0}, False),                       # the server answered, or never had it all
    ({"state": "error", "wire_begun": 1, "delivered": 1}, False),       # known to have gone
    ({"state": "queued", "wire_begun": 1}, False),
    ({"state": "sending", "wire_begun": 1}, False),                     # still going
])
def test_unsure_is_a_row_in_error_whose_whole_message_went_and_isnt_known_to_have_landed(kw, said):
    assert outbox_item.is_unsure(row(**kw)) is said
    assert listed(**kw)["unsure"] is said


def test_a_persons_retry_leaves_nothing_saying_unsure_or_retrying():
    again = listed(**{"state": "queued", "wire_begun": 1, "wait_kind": outbox_item.BACKOFF, **outbox_item.RETRIED})
    assert (again["unsure"], again["scheduled"], again["retrying"]) == (False, False, False)


# -- what it owes ---------------------------------------------------------------

def test_a_new_message_owes_a_sent_copy_and_nothing_a_reply_does():
    assert owed(row(), saves_sent=False) == outbox_item.Owed(sent_copy=True, sent_filed_for_us=False, answered=False,
                                                             followup=False, draft=False)


def test_where_the_server_files_sent_mail_only_the_flag_is_left():
    owes = owed(row(), saves_sent=True)
    assert (owes.sent_copy, owes.sent_filed_for_us) == (False, True)


@pytest.mark.parametrize("saves_sent", [True, False])
def test_a_sent_copy_once_flagged_is_never_owed_again(saves_sent):
    owes = owed(row(sent_appended=1), saves_sent=saves_sent)
    assert (owes.sent_copy, owes.sent_filed_for_us) == (False, False)


@pytest.mark.parametrize("original", [{"orig_message_id": "<o@example.test>"}, {"in_reply_to_pk": 7}])
def test_a_reply_owes_the_mark_and_the_follow_up_each_until_its_own_flag(original):
    both = owed(row(**original), saves_sent=True)
    assert (both.answered, both.followup) == (True, True)
    marked = owed(row(answered_set=1, **original), saves_sent=True)
    assert (marked.answered, marked.followup) == (False, True)
    noted = owed(row(followup_recorded=1, **original), saves_sent=True)
    assert (noted.answered, noted.followup) == (True, False)


def test_a_send_from_a_draft_owes_removing_it_until_that_is_done():
    assert owed(row(draft_id=4), saves_sent=True).draft is True
    assert owed(row(draft_id=4, draft_deleted=1), saves_sent=True).draft is False
    assert owed(row(draft_id=None, draft_deleted=0), saves_sent=True).draft is False


def test_a_row_delivered_before_this_pass_looks_in_sent_first():
    assert outbox_item.sent_copy_to_look_for(row(delivered=1)) == "<a@example.test>"
    assert outbox_item.sent_copy_to_look_for(row(delivered=0)) is None


def test_tries_count_from_the_delivery_not_on_from_the_sends():
    assert outbox_item.attempts_since_delivery(row(delivered=0, attempts=4)) == 0
    assert outbox_item.attempts_since_delivery(row(delivered=1, attempts=2)) == 2


# -- the original ---------------------------------------------------------------

def test_a_row_can_be_a_sends_original_only_in_its_account_and_with_no_message_id():
    send = row(orig_account="ada@example.test")
    assert outbox_item.still_the_original(send, {"account": "ada@example.test", "message_id": None}) is True
    assert outbox_item.still_the_original(send, {"account": "ada@example.test", "message_id": ""}) is True
    assert outbox_item.still_the_original(send, {"account": "bea@example.test", "message_id": None}) is False
    assert outbox_item.still_the_original(send, {"account": "ada@example.test", "message_id": "<x@example.test>"}) is False
    assert outbox_item.still_the_original(send, None) is False


def test_a_followup_goes_by_the_originals_thread_as_it_stands_and_else_as_it_was():
    send = row(orig_thread_id="rf:then")
    assert outbox_item.thread_of_reply(send, {"thread_id": "rf:now"}) == "rf:now"
    assert outbox_item.thread_of_reply(send, {"thread_id": None}) == "rf:then"
    assert outbox_item.thread_of_reply(send, None) == "rf:then"
    assert outbox_item.thread_of_reply(row(orig_thread_id=None), None) is None


# -- how long it waits ----------------------------------------------------------

def new_row(send_after, due=1000):
    built = {"raw": b"", "message_id": "<a@example.test>", "envelope": [], "to_disp": [], "cc_disp": [], "bcc_disp": []}
    return outbox_item.new_row(built, {"account": "ada@example.test", "idempotency_key": "k", "send_after": send_after},
                               None, due, "2026-11-02T12:00:00+00:00")


def test_a_send_with_no_time_waits_out_its_grace():
    r = new_row(0)
    assert (r["next_attempt_at"], r["wait_kind"]) == (1000, outbox_item.GRACE)


def test_a_time_inside_the_grace_window_changes_nothing():
    r = new_row(999)
    assert (r["next_attempt_at"], r["wait_kind"]) == (1000, outbox_item.GRACE)


def test_a_time_past_the_grace_window_is_scheduled():
    r = new_row(1001)
    assert (r["next_attempt_at"], r["wait_kind"]) == (1001, outbox_item.SCHEDULED)


@pytest.mark.parametrize("wait_kind, scheduled, retrying", [
    (outbox_item.GRACE, False, False), (outbox_item.SCHEDULED, True, False), (outbox_item.BACKOFF, False, True),
])
def test_the_list_says_what_a_queued_row_waits_for_from_its_kind(wait_kind, scheduled, retrying):
    r = listed(wait_kind=wait_kind)
    assert (r["scheduled"], r["retrying"]) == (scheduled, retrying)


def test_only_a_queued_row_waits():
    r = listed(wait_kind=outbox_item.BACKOFF, state="error")
    assert (r["scheduled"], r["retrying"]) == (False, False)


@pytest.mark.parametrize("kw, state, error", [
    ({"state": "queued", "delivered": 1, "sent_appended": 1}, "sent", None),
    ({"state": "error", "delivered": 1, "sent_appended": 1}, "sent", None),      # what's left of it asks nothing of anyone
    ({"state": "queued", "delivered": 1}, "sent", outbox_item.SENT_COPY_PENDING),
    ({"state": "error", "delivered": 1}, "error", outbox_item.SENT_COPY_FAILED),
])
def test_a_delivered_row_lists_as_sent_unless_its_sent_copy_gave_up(kw, state, error):
    r = listed(last_error="no", **kw)
    assert (r["state"], r["error"]) == (state, error)
