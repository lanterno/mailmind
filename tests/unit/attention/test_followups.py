"""What the minute's round asks of a thread, with no database, no clock and no I/O. test_followups_in_sql.py holds the
SQL that runs it to these."""
import dataclasses

import pytest

from mailmind.attention.domain.followups import (COMES_DUE, DISMISSED, DUE, EXPIRE_DAYS, GRACE_DAYS, MET, PENDING,
                                                 RECEIVED, RECEIVED_NUDGE, REPLY_LATER, SENT, FollowUp, Mail, Thread,
                                                 comes_due, dismissed, expired, met, nudge_for, on_reply_sent, pinned,
                                                 unpinning_ends)

NOW = 1_800_000_000
DAY = 86400
GRACE = GRACE_DAYS * DAY
LIFE = EXPIRE_DAYS * DAY


def ask(**mail):
    """The message that wants a nudge: read, unanswered and a matter for you, five days old."""
    return Mail(**{"pk": 1, "ts": NOW - 5 * DAY, "reply_worthy": True, **mail})


def asked(**thread):
    return Thread(**{"id": "T", "mail": (ask(),), **thread})


# ── a received nudge ──────────────────────────────────────────────────────────
def test_a_read_unanswered_thread_that_matters_wants_a_nudge():
    assert nudge_for(asked(), NOW) == ask()


BREAKS = {
    "in_a_thread": asked(id=""),
    "read": asked(mail=(ask(seen=False),)),
    "unanswered": asked(mail=(ask(answered=True),)),
    "not_junk": asked(mail=(ask(junk=True),)),
    "past_the_grace": asked(mail=(ask(ts=NOW - GRACE + 1),)),
    "within_its_lifetime": asked(mail=(ask(ts=NOW - LIFE - 1),)),
    "the_threads_newest": None,     # some message is always the newest, unless all are drafts: below
    "not_snoozed": asked(mail=(ask(snoozed=True),)),
    "worth_a_reply": asked(mail=(ask(reply_worthy=False),)),
    "nothing_from_me": asked(mail=(ask(), ask(pk=2, ts=NOW - 6 * DAY, mine=True, reply_worthy=False))),
    "not_muted": asked(muted=True),
    "no_follow_up_yet": asked(follow_up=FollowUp(SENT, PENDING)),
}


def test_every_condition_has_a_case():
    assert BREAKS.keys() == RECEIVED_NUDGE.keys()


@pytest.mark.parametrize("name", [n for n, t in BREAKS.items() if t])
def test_each_condition_alone_stops_a_nudge(name):
    thread = BREAKS[name]
    assert nudge_for(thread, NOW) is None
    assert [n for n, holds in RECEIVED_NUDGE.items() if not holds(thread, thread.newest, NOW)] == [name]


def test_a_thread_of_drafts_has_no_newest_and_wants_no_nudge():
    assert nudge_for(asked(mail=(ask(draft=True),)), NOW) is None


def test_it_waits_out_the_grace_exactly():
    assert nudge_for(asked(mail=(ask(ts=NOW - GRACE),)), NOW) is not None
    assert nudge_for(asked(mail=(ask(ts=NOW - GRACE + 1),)), NOW) is None


def test_its_floor_is_inclusive_where_expiry_is_strict():
    """A nudge at the floor isn't expired, and one past it isn't detected: no state where one is added and ended in turn."""
    at_floor, past = NOW - LIFE, NOW - LIFE - 1
    assert nudge_for(asked(mail=(ask(ts=at_floor),)), NOW) is not None
    assert not expired(FollowUp(RECEIVED, DUE, last_msg_ts=at_floor), NOW)
    assert nudge_for(asked(mail=(ask(ts=past),)), NOW) is None
    assert expired(FollowUp(RECEIVED, DUE, last_msg_ts=past), NOW)


def test_it_anchors_on_the_threads_newest_not_on_what_made_it_matter():
    worthy, newest = ask(pk=1, ts=NOW - 9 * DAY), ask(pk=2, ts=NOW - 5 * DAY, reply_worthy=False)
    assert nudge_for(asked(mail=(worthy, newest)), NOW) == newest


def test_the_newest_goes_by_the_higher_pk_on_a_tie():
    assert asked(mail=(ask(pk=3), ask(pk=4), ask(pk=2))).newest == ask(pk=4)


def test_a_draft_of_a_reply_is_neither_the_newest_nor_a_reply():
    draft = ask(pk=2, ts=NOW - DAY, mine=True, draft=True, reply_worthy=False)
    assert nudge_for(asked(mail=(ask(), draft)), NOW) == ask()


def test_only_the_newest_has_to_be_unread_answered_or_snoozed_for_it_to_stop():
    older = ask(pk=1, ts=NOW - 9 * DAY, seen=False, answered=True, snoozed=True, junk=True)
    newest = ask(pk=2, reply_worthy=False)
    assert nudge_for(asked(mail=(older, newest)), NOW) == newest


@pytest.mark.parametrize("state", [PENDING, DUE, DISMISSED])
@pytest.mark.parametrize("kind", [SENT, RECEIVED, REPLY_LATER])
def test_any_follow_up_on_the_thread_stops_one_a_dismissed_one_for_good(kind, state):
    assert nudge_for(asked(follow_up=FollowUp(kind, state)), NOW) is None


# ── a sent follow-up comes due ────────────────────────────────────────────────
def waiting(**follow_up):
    return FollowUp(**{"kind": SENT, "state": PENDING, "first_sent_ts": NOW - GRACE, **follow_up})


def replied_to(*mail, **thread):
    return Thread("T", mail, **thread)


MINE = Mail(pk=1, ts=NOW - GRACE, mine=True)


def test_a_sent_one_comes_due_when_the_grace_has_gone_by_to_the_second():
    assert comes_due(waiting(), replied_to(MINE), NOW)
    assert not comes_due(waiting(first_sent_ts=NOW - GRACE + 1), replied_to(MINE), NOW)


def test_without_a_send_time_it_never_comes_due():
    assert not comes_due(waiting(first_sent_ts=None), replied_to(MINE), NOW)


def test_an_answer_since_keeps_it_from_coming_due_and_only_a_later_one():
    answer = Mail(pk=2, ts=NOW - GRACE + 1)
    assert not comes_due(waiting(), replied_to(MINE, answer), NOW)
    same_second = Mail(pk=2, ts=NOW - GRACE)
    assert comes_due(waiting(), replied_to(MINE, same_second), NOW)


def test_more_of_my_own_mail_is_no_answer():
    assert comes_due(waiting(), replied_to(MINE, Mail(pk=2, ts=NOW - DAY, mine=True)), NOW)


def test_a_muted_thread_doesnt_come_due():
    assert not comes_due(waiting(), replied_to(MINE, muted=True), NOW)


@pytest.mark.parametrize("kind, state", [(SENT, DUE), (SENT, DISMISSED), (RECEIVED, PENDING), (REPLY_LATER, PENDING)])
def test_only_a_pending_sent_one_comes_due(kind, state):
    assert not comes_due(waiting(kind=kind, state=state), replied_to(MINE), NOW)


def test_every_way_of_coming_due_is_asked():
    assert list(COMES_DUE) == ["sent_and_pending", "past_the_grace", "no_answer_since", "not_muted"]


# ── a received nudge expires ──────────────────────────────────────────────────
@pytest.mark.parametrize("state", [PENDING, DUE])
def test_a_live_nudge_expires_strictly_past_its_lifetime(state):
    assert expired(FollowUp(RECEIVED, state, last_msg_ts=NOW - LIFE - 1), NOW)
    assert not expired(FollowUp(RECEIVED, state, last_msg_ts=NOW - LIFE), NOW)


def test_a_nudge_of_unknown_age_never_expires():
    assert not expired(FollowUp(RECEIVED, DUE, last_msg_ts=None), NOW)


def test_a_dismissal_doesnt_expire():
    assert not expired(FollowUp(RECEIVED, DISMISSED, last_msg_ts=NOW - 2 * LIFE), NOW)


@pytest.mark.parametrize("kind", [SENT, REPLY_LATER])
def test_only_a_received_nudge_expires(kind):
    assert not expired(FollowUp(kind, DUE, last_msg_ts=NOW - 2 * LIFE), NOW)


# ── a commitment is met ───────────────────────────────────────────────────────
THEIRS = Mail(pk=2, ts=NOW)


@pytest.mark.parametrize("kind", [SENT, RECEIVED, REPLY_LATER])
@pytest.mark.parametrize("state", [PENDING, DUE])
def test_a_mute_meets_any_live_one(kind, state):
    assert met(FollowUp(kind, state), Thread("T", muted=True))


@pytest.mark.parametrize("kind", [SENT, RECEIVED, REPLY_LATER])
@pytest.mark.parametrize("thread", [Thread("T", muted=True), Thread("T", (THEIRS, Mail(pk=3, ts=NOW, mine=True), Mail(pk=4, ts=NOW, answered=True)))])
def test_a_dismissal_is_never_met_so_it_stays(kind, thread):
    assert not met(FollowUp(kind, DISMISSED), thread)


def test_someone_answering_meets_a_sent_one():
    sent = FollowUp(SENT, DUE, first_sent_ts=NOW - DAY)
    assert met(sent, Thread("T", (THEIRS,)))
    assert not met(sent, Thread("T", (dataclasses.replace(THEIRS, ts=NOW - DAY),)))     # no later than the send
    assert not met(sent, Thread("T", (Mail(pk=3, ts=NOW, mine=True),)))                 # more of mine


def test_a_sent_one_with_no_send_time_counts_from_the_beginning():
    assert met(FollowUp(SENT, DUE), Thread("T", (THEIRS,)))


@pytest.mark.parametrize("kind", [RECEIVED, REPLY_LATER])
def test_my_reply_meets_a_received_one_or_a_pin(kind):
    assert met(FollowUp(kind, DUE), Thread("T", (Mail(pk=3, ts=NOW, mine=True),)))


@pytest.mark.parametrize("kind", [RECEIVED, REPLY_LATER])
def test_the_servers_answered_flag_meets_them_too(kind):
    assert met(FollowUp(kind, DUE), Thread("T", (Mail(pk=3, ts=NOW, answered=True),)))


@pytest.mark.parametrize("kind", [RECEIVED, REPLY_LATER])
def test_a_draft_of_a_reply_doesnt_meet_them(kind):
    assert not met(FollowUp(kind, DUE), Thread("T", (Mail(pk=3, ts=NOW, mine=True, draft=True),)))


@pytest.mark.parametrize("kind", [RECEIVED, REPLY_LATER])
def test_more_of_their_mail_doesnt_meet_them(kind):
    assert not met(FollowUp(kind, DUE, first_sent_ts=NOW - DAY), Thread("T", (THEIRS,)))


def test_my_reply_doesnt_meet_a_sent_one():
    assert not met(FollowUp(SENT, DUE, first_sent_ts=NOW - DAY), Thread("T", (Mail(pk=3, ts=NOW, mine=True, answered=True),)))


def test_every_way_of_meeting_one_is_asked():
    assert list(MET) == ["muted", "they_answered", "i_replied"]


# ── what a person or a send does to one ──────────────────────────────────────
def on_thread(kind, state):
    return FollowUp(kind, state, first_sent_ts=NOW - DAY, last_msg_ts=NOW - DAY, due_ts=NOW - 1, created_at="then", resolved_at="once")


def test_a_reply_starts_a_sent_one_on_a_thread_with_none():
    assert on_reply_sent(None, NOW, "now") == FollowUp(SENT, PENDING, first_sent_ts=NOW, last_msg_ts=NOW, created_at="now")


@pytest.mark.parametrize("kind", [SENT, RECEIVED, REPLY_LATER])
@pytest.mark.parametrize("state", [PENDING, DUE, DISMISSED])
def test_a_thread_has_one_so_a_reply_leaves_what_is_there(kind, state):
    assert on_reply_sent(on_thread(kind, state), NOW, "now") is None


def test_a_pin_on_a_thread_with_none_is_due_at_once():
    assert pinned(None, NOW - DAY, "now") == FollowUp(REPLY_LATER, DUE, last_msg_ts=NOW - DAY, created_at="now")


@pytest.mark.parametrize("kind", [SENT, RECEIVED])
def test_a_pin_supersedes_a_nudge(kind):
    f = pinned(on_thread(kind, DUE), NOW, "now")
    assert (f.kind, f.state, f.last_msg_ts, f.created_at) == (REPLY_LATER, DUE, NOW, "now")


@pytest.mark.parametrize("kind", [SENT, RECEIVED, REPLY_LATER])
def test_a_pin_re_activates_a_dismissal_and_nothing_else_does(kind):
    was = on_thread(kind, DISMISSED)
    f = pinned(was, NOW, "now")
    assert (f.kind, f.state, f.resolved_at) == (REPLY_LATER, DUE, None)
    assert on_reply_sent(was, NOW, "now") is None and not met(was, Thread("T", muted=True)) and not comes_due(was, Thread("T"), NOW)


def test_a_pin_keeps_when_a_send_went_out_and_came_due():
    f = pinned(on_thread(SENT, DUE), NOW, "now")
    assert (f.first_sent_ts, f.due_ts) == (NOW - DAY, NOW - 1)


def test_a_thread_with_no_mail_left_pins_with_no_time():
    assert pinned(None, None, "now").last_msg_ts is None


@pytest.mark.parametrize("kind", [SENT, RECEIVED, REPLY_LATER])
@pytest.mark.parametrize("state", [PENDING, DUE, DISMISSED])
def test_dismissing_keeps_the_row_whatever_it_was(kind, state):
    f = dismissed(on_thread(kind, state), "now")
    assert (f.kind, f.state, f.resolved_at, f.first_sent_ts) == (kind, DISMISSED, "now", NOW - DAY)


def test_dismissing_nothing_writes_nothing():
    assert dismissed(None, "now") is None


@pytest.mark.parametrize("state", [PENDING, DUE, DISMISSED])
def test_un_pinning_ends_a_pin_whatever_its_state(state):
    assert unpinning_ends(on_thread(REPLY_LATER, state))


@pytest.mark.parametrize("current", [None, on_thread(SENT, DUE), on_thread(RECEIVED, DUE), on_thread(RECEIVED, DISMISSED)])
def test_un_pinning_leaves_anything_else(current):
    assert not unpinning_ends(current)
