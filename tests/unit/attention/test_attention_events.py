"""What Attention's use cases tell the rest of the app on the bus, once a change has committed: a mute, a snooze, a wake, a
follow-up round, a dismissal and a pin each say so, and a repeat that changes nothing or a request refused says nothing. Each use
case runs on in-memory fakes behind its ports. That the app hears each is tests/integration/attention/test_attention_events.py's."""
from contextlib import contextmanager

import pytest

from mailmind.attention.application.followups import FollowUps
from mailmind.attention.application.muting import Muting
from mailmind.attention.application.snoozing import Snoozing
from mailmind.attention.application.threads import Threads
from mailmind.attention.domain.events import (FollowUpDismissed, FollowUpsChanged, ReplyLaterChanged, SnoozeLifted, SnoozeSet,
                                              SnoozeWoke, ThreadMuted, ThreadUnmuted)
from mailmind.kernel.errors import Refused
from mailmind.platform import bus

ALL = (SnoozeSet, SnoozeLifted, SnoozeWoke, ThreadMuted, ThreadUnmuted, FollowUpsChanged, FollowUpDismissed, ReplyLaterChanged)
ADA = "ada@example.test"
THREAD = "rf:<lunch@example.test>"
LUNCH = {"pk": 7, "account": ADA, "folder": "INBOX", "message_key": 41, "thread_id": THREAD, "seen": 0, "snooze_until": None}


@pytest.fixture
def heard():
    """What each event's subscribers hear, kept beside them."""
    got = []
    stops = [bus.subscribe(topic, got.append) for topic in ALL]
    yield got
    for stop in stops:
        stop()


class Uow:
    """A unit of work with no database: the fakes below keep what they're told."""
    @contextmanager
    def read(self):
        yield None

    write = read


class Book:
    """Attention's book, as far as these use cases ask it."""
    def __init__(self):
        self.muted, self.snoozed, self.due, self.next_due = set(), {}, [], set()
        self.due_keys = set()

    def is_muted(self, c, account, thread_id):
        return (account, thread_id) in self.muted

    def unread_in_thread(self, c, account, thread_id):
        return [LUNCH] if (account, thread_id) not in self.muted else []

    def a_member(self, c, account, thread_id):
        return LUNCH

    def mute_thread(self, c, account, thread_id, when):
        self.muted.add((account, thread_id))

    def unmute_thread(self, c, account, thread_id):
        self.muted.discard((account, thread_id))

    def thread_pks(self, c, account, thread_id):
        return [LUNCH["pk"]]

    def set_snooze(self, c, pk, until):
        self.snoozed[pk] = until

    def clear_snooze(self, c, pk):
        self.snoozed.pop(pk, None)

    def due_snoozes(self, c, now_ts):
        return list(self.due)

    def due_followup_keys(self, c):
        return set(self.due_keys)

    def resolve_followups(self, c, account):
        pass

    def expire_followups(self, c, now_ts, days):
        pass

    def followup_promote_due(self, c, now_ts, days):
        self.due_keys = set(self.next_due)

    def followup_detect_received(self, c, now_ts, days, max_age_days):
        pass

    def dismiss_followup(self, c, account, thread_id, when):
        pass

    def set_replylater(self, c, account, thread_id, when):
        pass

    def clear_replylater(self, c, account, thread_id):
        pass


class Mail:
    """The Mailbox's side of a message: its row, and the flags queued for its server."""
    def get(self, c, pk):
        return LUNCH if pk == LUNCH["pk"] else None

    def queue_flags(self, c, row, flags, mutation_id, when):
        return True

    def flags_queued(self):
        pass


class Accounts:
    def emails(self):
        return [ADA]


class Undo:
    def push(self, entry, group):
        pass


def muting(book):
    mail = Mail()
    return Muting(Uow(), book, mail, Threads(mail, Accounts()), Undo())


def snoozing(book):
    return Snoozing(Uow(), book, Mail(), Undo())


def follow_ups(book):
    return FollowUps(Uow(), book, Threads(Mail(), Accounts()), Accounts())


THE_THREAD = {"account": ADA, "thread_id": THREAD}


# -- a mute ----------------------------------------------------------------------------------------------------------

def test_a_mute_says_so_and_a_repeat_says_nothing(heard):
    use = muting(Book())
    use.mute(THE_THREAD)
    assert heard == [ThreadMuted(ADA, THREAD)]
    use.mute(THE_THREAD)
    assert heard == [ThreadMuted(ADA, THREAD)]


def test_an_unmute_says_so(heard):
    muting(Book()).unmute(THE_THREAD)
    assert heard == [ThreadUnmuted(ADA, THREAD)]


def test_a_refused_mute_says_nothing(heard):
    use = muting(Book())
    for ask in (use.mute, use.unmute):
        with pytest.raises(Refused, match="bad mute target"):
            ask({})
    assert heard == []


# -- a snooze --------------------------------------------------------------------------------------------------------

def test_a_snooze_and_its_lifting_say_how_many(heard):
    use = snoozing(Book())
    use.snooze({"pks": [LUNCH["pk"]], "preset": "tomorrow"})
    use.unsnooze({"pks": [LUNCH["pk"]]})
    assert heard == [SnoozeSet(1), SnoozeLifted(1)]


def test_a_refused_snooze_says_nothing(heard):
    with pytest.raises(Refused, match="bad snooze time"):
        snoozing(Book()).snooze({"pks": [LUNCH["pk"]], "until": "soon"})
    assert heard == []


def test_a_wake_says_how_many_in_which_accounts_and_no_wake_says_nothing(heard):
    book = Book()
    use = snoozing(book)
    use.wake_due()
    assert heard == []
    book.due = [LUNCH]
    use.wake_due()
    assert heard == [SnoozeWoke(1, (ADA,))]


# -- a follow-up -----------------------------------------------------------------------------------------------------

def test_a_round_says_so_only_when_what_is_due_changes(heard):
    book = Book()
    use = follow_ups(book)
    use.tick()
    assert heard == []
    book.next_due = {(ADA, THREAD)}
    use.tick()
    assert heard == [FollowUpsChanged(1)]
    use.tick()
    assert heard == [FollowUpsChanged(1)]


def test_a_round_that_swaps_one_due_thread_for_another_says_so(heard):
    book = Book()
    book.due_keys = {(ADA, THREAD)}
    book.next_due = {(ADA, "rf:<other@example.test>")}
    follow_ups(book).tick()
    assert heard == [FollowUpsChanged(1)]


def test_a_dismissal_says_so_whether_or_not_there_was_one(heard):
    follow_ups(Book()).dismiss(THE_THREAD)
    assert heard == [FollowUpDismissed(ADA, THREAD)]


def test_a_pin_and_an_un_pin_say_so(heard):
    use = follow_ups(Book())
    use.reply_later(THE_THREAD)
    use.reply_later({**THE_THREAD, "on": False})
    assert heard == [ReplyLaterChanged(ADA, THREAD)] * 2


def test_a_refused_dismissal_or_pin_says_nothing(heard):
    use = follow_ups(Book())
    for ask in (use.dismiss, use.reply_later):
        with pytest.raises(Refused, match="bad target"):
            ask({})
    assert heard == []
