"""mailmind.platform.sse: the live event stream's hub. Events are numbered and the last RING kept,
so a tab that reconnects with the id of the last event it saw resumes after it, and one the hub
can't resume gets a single resync."""
import threading
import time

import pytest

from mailmind.platform import sse
from mailmind.platform.sse import RESYNC, Event, Hub


def topics(events):
    return [(e.id, e.topic, e.data) for e in events]


def test_events_are_numbered_from_one_under_the_hubs_run():
    hub = Hub(run="r1")
    assert hub.publish("mute", {"account": "ada@example.test"}) == "r1-1"
    assert hub.publish("followup", {"count": 2}) == "r1-2"


def test_a_new_listener_starts_from_now_and_says_so_first():
    hub = Hub(run="r1")
    hub.publish("mute", {})
    tab = hub.listen()
    assert tab.opening() == b"retry: 3000\nid: r1-1\n: connected\n\n"
    assert tab.next(0) == []                                     # nothing before it connected
    hub.publish("outbox", {})
    assert topics(tab.next(0)) == [("r1-2", "outbox", {})]
    assert tab.last_id == "r1-2"


def test_before_any_event_the_position_is_zero():
    tab = Hub(run="r1").listen()
    assert tab.last_id == "r1-0"


def test_a_tab_that_reconnects_hears_what_it_missed_in_order():
    hub = Hub(run="r1")
    hub.publish("mute", {"account": "ada@example.test"})
    seen = hub.publish("snooze-wake", {"count": 1})
    hub.publish("followup", {"count": 1})
    hub.publish("outbox", {})
    tab = hub.listen(seen)
    assert tab.opening().startswith(b"retry: 3000\nid: r1-2\n")
    assert topics(tab.next(0)) == [("r1-3", "followup", {"count": 1}), ("r1-4", "outbox", {})]
    assert tab.next(0) == []


def test_a_tab_that_saw_everything_hears_only_whats_new():
    hub = Hub(run="r1")
    last = hub.publish("mute", {})
    tab = hub.listen(last)
    assert tab.next(0) == []


@pytest.mark.parametrize("last", ["r0-3", "r1-9", "r1-x", "nonsense", "-1", "r1--1"],
                         ids=["an-earlier-run", "ahead-of-the-hub", "not-a-number", "no-run", "bare", "negative"])
def test_an_id_the_hub_cant_resume_from_gets_one_resync(last):
    hub = Hub(run="r1")
    for _ in range(3):
        hub.publish("mute", {})
    tab = hub.listen(last)
    assert topics(tab.next(0)) == [("r1-3", RESYNC, {})]
    assert tab.next(0) == []                                     # once
    hub.publish("outbox", {})
    assert topics(tab.next(0)) == [("r1-4", "outbox", {})]


def test_a_tab_further_back_than_the_ring_gets_a_resync_and_one_at_its_edge_does_not():
    hub = Hub(ring=3, run="r1")
    for _ in range(5):
        hub.publish("mute", {})                                  # keeps r1-3, r1-4, r1-5
    assert topics(hub.listen("r1-1").next(0)) == [("r1-5", RESYNC, {})]
    assert [e.id for e in hub.listen("r1-2").next(0)] == ["r1-3", "r1-4", "r1-5"]


def test_a_listener_that_falls_behind_the_ring_gets_a_resync_not_a_gap():
    hub = Hub(ring=3, run="r1")
    tab = hub.listen()
    for _ in range(5):
        hub.publish("mute", {})
    assert topics(tab.next(0)) == [("r1-5", RESYNC, {})]
    hub.publish("outbox", {})
    assert [e.topic for e in tab.next(0)] == ["outbox"]


def test_a_slow_listener_loses_nothing_the_ring_still_holds():
    hub = Hub(ring=512, run="r1")
    tab = hub.listen()
    for i in range(200):                                         # far past the old 64-event queue
        hub.publish("followup", {"count": i})
    got = tab.next(0)
    assert [e.data["count"] for e in got] == list(range(200))


def test_every_listener_hears_each_event():
    hub = Hub(run="r1")
    a, b = hub.listen(), hub.listen()
    hub.publish("mute", {"account": "cy@example.test"})
    assert topics(a.next(0)) == topics(b.next(0)) == [("r1-1", "mute", {"account": "cy@example.test"})]


def test_a_waiting_listener_wakes_when_an_event_comes():
    hub = Hub(run="r1")
    tab = hub.listen()
    got = []
    t = threading.Thread(target=lambda: got.extend(tab.next(5)))
    t.start()
    time.sleep(0.05)
    began = time.monotonic()
    hub.publish("outbox", {})
    t.join(5)
    assert [e.topic for e in got] == ["outbox"]
    assert time.monotonic() - began < 1


def test_a_quiet_wait_ends_empty_at_its_timeout():
    tab = Hub(run="r1").listen()
    began = time.monotonic()
    assert tab.next(0.05) == []
    assert time.monotonic() - began >= 0.04


def test_listeners_past_the_cap_are_turned_away_until_one_closes():
    hub = Hub(max_listeners=2, run="r1")
    a, b = hub.listen(), hub.listen("r1-0")
    assert a and b and hub.listen() is None
    assert hub.listening() == 2
    a.close()
    a.close()                                                    # once only
    assert hub.listening() == 1
    assert hub.listen() is not None


def test_closing_wakes_a_waiting_listener():
    hub = Hub(run="r1")
    tab = hub.listen()
    got = []
    t = threading.Thread(target=lambda: got.append(tab.next(5)))
    t.start()
    time.sleep(0.05)
    tab.close()
    t.join(5)
    assert got == [[]] and not t.is_alive()


def test_only_the_streams_topics_can_be_published():
    with pytest.raises(ValueError, match="not one of the stream's topics"):
        Hub().publish("subject-changed", {})
    assert RESYNC not in sse.TOPICS                              # the hub's own, never published


def test_a_kept_event_is_a_copy_of_the_data_it_was_given():
    hub = Hub(run="r1")
    tab = hub.listen()
    data = {"count": 1}
    hub.publish("followup", data)
    data["count"] = 99
    assert tab.next(0)[0].data == {"count": 1}


def test_a_frame_is_id_event_and_json_data():
    assert Event("r1-7", "draft-ready", {"account": "ada@example.test", "pk": 4}).frame() == \
        b'id: r1-7\nevent: draft-ready\ndata: {"account": "ada@example.test", "pk": 4}\n\n'


def test_each_hub_draws_its_own_run():
    assert Hub().run != Hub().run
    assert sse.publish("outbox", {}).startswith(sse.HUB.run + "-")


def test_the_page_listens_for_every_topic_for_resync_and_hello_and_nothing_else():
    import os
    import re
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "web", "assets", "legacy", "app.js")
    with open(path, encoding="utf-8") as f:
        heard = set(re.findall(r'\bes\.addEventListener\("([a-z-]+)"', f.read()))
    assert heard == sse.TOPICS | {RESYNC, sse.HELLO}
