"""That the app hears what Attention tells it, once wired. What each use case says, and when it says nothing,
tests/unit/attention/test_attention_events.py holds; what the tabs then hear, frame by frame,
tests/api/test_http_events.py and its goldens."""
from mailmind.attention.domain.events import (FollowUpDismissed, FollowUpsChanged, ReplyLaterChanged, SnoozeLifted, SnoozeSet,
                                              SnoozeWoke, ThreadMuted, ThreadUnmuted)
from mailmind.bootstrap import wiring
from mailmind.platform.bus import BUS

ALL = (SnoozeSet, SnoozeLifted, SnoozeWoke, ThreadMuted, ThreadUnmuted, FollowUpsChanged, FollowUpDismissed, ReplyLaterChanged)


def test_each_is_heard_once_wired(app):
    wiring.wire()
    assert all(BUS.subscribers(topic) for topic in ALL)
    wiring.unwire()
    assert not any(BUS.subscribers(topic) for topic in ALL)
    wiring.wire()
