"""The tests of tests/api/test_http_events.py that cut the event hub's constants, run against the app in this process.
The golden files are in tests/golden/http_events_in_process/, each event's id written <run>-<n>.

A served app keeps its hub as it is: a keepalive every 20 seconds, a cap of 8 listeners and the last 512 events kept for
tabs that reconnect. These tests want a keepalive within the test, a cap of one and a ring of two, which only a hub in
this process can be given. The keepalive is cut to a fifth of a second, so a closed listener is noticed (and its thread
ends) within the test.
"""
import pytest

from event_stream import CONNECTION_HEADERS, Stream
from waiting import wait_until

READ_THREAD = "ada-inbox-04-ci.eml"                     # muting it queues nothing: no `outbox` event among these


@pytest.fixture
def quick(monkeypatch):
    """The stream's hub, with the keepalive cut to a fifth of a second."""
    from mailmind.platform import sse
    monkeypatch.setattr(sse, "HEARTBEAT", 0.2)
    return sse


def subscribers(sse, n):
    wait_until(lambda: sse.HUB.listening() == n)
    return sse.HUB.listening()


def test_a_listener_hears_each_event_then_a_keepalive(app, golden, world, quick):
    s = Stream(app)
    golden.note("status and headers", {"status": s.status, "headers": {k: v for k, v in s.headers.items()
                                                                       if k not in CONNECTION_HEADERS}})
    golden.note("first frame", s.frame())
    lunch = world.copy(READ_THREAD)
    app.post("/api/mute", lunch)
    app.post("/api/undo", {})
    app.post("/api/replylater", lunch)
    golden.note("frames for mute, undo and Reply Later", [s.event() for _ in range(3)])
    golden.note("then, with nothing to say", s.frame())
    s.close()
    golden.note("listeners once it hangs up", subscribers(quick, 0))


def test_a_listener_past_the_cap_is_turned_away(app, golden, quick, monkeypatch):
    monkeypatch.setattr(quick.HUB, "max_listeners", 1)
    s = Stream(app)
    s.frame()
    golden.record("a second listener", app.get("/api/events"))
    s.close()
    subscribers(quick, 0)
    t = Stream(app)
    golden.note("a listener after the first hung up", {"status": t.status, "first frame": t.frame()})
    t.close()
    subscribers(quick, 0)


def test_a_tab_that_reconnects_hears_what_it_missed(app, golden, world, quick):
    lunch = world.copy(READ_THREAD)
    s = Stream(app)
    golden.note("first frame", s.frame())
    app.post("/api/mute", lunch)
    heard = s.event()
    s.close()                                                     # the tab drops, say a laptop lid
    subscribers(quick, 0)
    app.post("/api/undo", {})
    app.post("/api/replylater", lunch)
    last = heard.split("\n")[0].removeprefix("id: ")
    t = Stream(app, **{"Last-Event-ID": last})
    golden.note("after the mute, it reconnects and hears", [t.frame() for _ in range(3)])
    golden.note("then, with nothing to say", t.frame())
    t.close()
    subscribers(quick, 0)


def test_a_tab_further_back_than_the_hub_keeps_is_told_to_fetch_afresh(app, golden, world, quick, monkeypatch):
    monkeypatch.setattr(quick, "HUB", quick.Hub(ring=2, run="small"))
    lunch = world.copy(READ_THREAD)
    for _ in range(2):
        app.post("/api/mute", lunch)
        app.post("/api/undo", {})
    t = Stream(app, **{"Last-Event-ID": "small-1"})
    golden.note("it reconnects and hears", [t.frame(), t.frame()])
    t.close()
    subscribers(quick, 0)
