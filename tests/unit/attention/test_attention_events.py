"""What Attention tells the rest of the app, once a change has committed, and that each is heard. What the tabs then hear,
frame by frame, tests/characterization/test_http_events.py and its goldens hold."""
import time
from datetime import timedelta

import pytest

from fixtures import world as w
from mailmind.attention.domain import followups
from mailmind.attention.domain.events import (FollowUpDismissed, FollowUpsChanged, ReplyLaterChanged, SnoozeLifted, SnoozeSet,
                                              SnoozeWoke, ThreadMuted, ThreadUnmuted)
from mailmind.bootstrap import attention, wiring
from mailmind.bootstrap.mailbox import store
from mailmind.platform import bus
from mailmind.platform.bus import BUS

ALL = (SnoozeSet, SnoozeLifted, SnoozeWoke, ThreadMuted, ThreadUnmuted, FollowUpsChanged, FollowUpDismissed, ReplyLaterChanged)
CI = "ada-inbox-04-ci.eml"              # already read
RECEIPT = "ada-inbox-05-receipt.eml"


@pytest.fixture
def heard():
    """What each event's subscribers hear, kept beside them."""
    got = []
    stops = [bus.subscribe(topic, got.append) for topic in ALL]
    yield got
    for stop in stops:
        stop()


def test_each_is_heard_once_wired(app):
    wiring.wire()
    assert all(BUS.subscribers(topic) for topic in ALL)
    wiring.unwire()
    assert not any(BUS.subscribers(topic) for topic in ALL)
    wiring.wire()


def test_a_mute_says_so_and_a_repeat_says_nothing(app, world, heard):
    thread = world.row(CI)["thread_id"]
    app.post("/api/mute", {"pk": world.pk(CI)})
    assert heard == [ThreadMuted(w.ADA, thread)]
    app.post("/api/mute", {"pk": world.pk(CI)})
    assert heard == [ThreadMuted(w.ADA, thread)]


def test_an_unmute_says_so(app, world, heard):
    thread = world.row(CI)["thread_id"]
    app.post("/api/unmute", {"pk": world.pk(CI)})
    assert heard == [ThreadUnmuted(w.ADA, thread)]


def test_a_refused_mute_says_nothing(app, heard):
    assert app.post("/api/mute", {}).status == 400
    assert app.post("/api/unmute", {}).status == 400
    assert heard == []


def test_a_snooze_and_its_lifting_say_how_many(app, world, heard):
    pk = world.pk(RECEIPT)
    app.post("/api/snooze", {"pk": pk, "preset": "tomorrow"})
    app.post("/api/unsnooze", {"pk": pk})
    assert heard == [SnoozeSet(1), SnoozeLifted(1)]


def test_a_refused_snooze_says_nothing(app, world, heard):
    assert app.post("/api/snooze", {"pk": world.pk(RECEIPT), "until": "soon"}).status == 400
    assert heard == []


def test_a_wake_says_how_many_in_which_accounts_and_no_wake_says_nothing(app, world, heard):
    app.post("/api/snooze", {"pk": world.pk(RECEIPT), "preset": "tomorrow"})
    del heard[:]
    attention.snoozing.wake_due()
    assert heard == []
    app.clock.shift(timedelta(days=2))
    attention.snoozing.wake_due()
    assert heard == [SnoozeWoke(1, (w.ADA,))]


def test_a_round_says_so_only_when_what_is_due_changes(app, world, heard):
    attention.followups.tick()
    assert heard == []
    c = store.conn()
    attention.book.followup_record_sent(c, w.ADA, world.row(CI)["thread_id"], None, int(time.time()), "2026-11-01T00:00:00+00:00")
    c.commit()
    c.close()
    attention.followups.tick()                  # it waits out its grace
    assert heard == []
    app.clock.shift(timedelta(days=followups.GRACE_DAYS + 1))
    attention.followups.tick()
    assert heard == [FollowUpsChanged(1)]
    attention.followups.tick()
    assert heard == [FollowUpsChanged(1)]


def test_a_dismissal_says_so_whether_or_not_there_was_one(app, world, heard):
    thread = world.row(CI)["thread_id"]
    app.post("/api/followups/dismiss", {"pk": world.pk(CI)})
    assert heard == [FollowUpDismissed(w.ADA, thread)]


def test_a_pin_and_an_un_pin_say_so(app, world, heard):
    thread = world.row(CI)["thread_id"]
    app.post("/api/replylater", {"pk": world.pk(CI)})
    app.post("/api/replylater", {"pk": world.pk(CI), "on": False})
    assert heard == [ReplyLaterChanged(w.ADA, thread)] * 2


def test_a_refused_dismissal_or_pin_says_nothing(app, heard):
    assert app.post("/api/followups/dismiss", {}).status == 400
    assert app.post("/api/replylater", {}).status == 400
    assert heard == []
