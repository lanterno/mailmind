"""The "since you last looked" delta: how its window is held and when it is written again."""
import time
from datetime import datetime, timedelta

from fixtures import world as w
from assistant_world import LUNCH
from world_helpers import UTC


def test_hwf_a_stale_watermark_keeps_one_delta_for_ten_minutes(app, world, ai):
    assert app.post("/api/briefing/since", {"since": 0}).json["cached"] is False
    app.clock.shift(timedelta(minutes=5))            # the 24h floor moved, but not to a new 10-minute step
    assert app.post("/api/briefing/since", {"since": 0}).json["cached"] is True
    assert len(ai.requests) == 1


def test_hwf_a_watermark_ahead_of_the_server_is_held_to_now(app, world, ai):
    world.deliver(w.ADA, "INBOX", w.variant(LUNCH, Message_ID="<lunch-future@lin.example.test>",
                                            Date="Mon, 02 Nov 2026 14:00:00 +0000"))    # dated two hours ahead
    now = int(time.time())
    first = app.post("/api/briefing/since", {"since": now + 3600}).json
    later = app.post("/api/briefing/since", {"since": now + 5400}).json
    assert (first["count"], first["cached"], later["cached"]) == (1, False, True)
    assert len(ai.requests) == 1


def test_hwf_a_delta_is_rewritten_when_its_window_grows(app, world, ai):
    since = int(time.time()) - 6 * 3600
    n = app.post("/api/briefing/since", {"since": since}).json["count"]
    assert app.post("/api/briefing/since", {"since": since}).json["cached"] is True
    world.deliver(w.ADA, "INBOX", w.variant(LUNCH, Message_ID="<lunch-late@lin.example.test>",
                                            Date="Mon, 02 Nov 2026 11:50:00 +0000"))
    grown = app.post("/api/briefing/since", {"since": since}).json
    assert (grown["count"], grown["cached"]) == (n + 1, False)
    assert len(ai.requests) == 2


def test_hwf_the_delta_counts_newsletters_but_not_scams(app, world, ai):
    world.deliver(w.ADA, "INBOX", w.variant("ada-inbox-03-newsletter.eml", Message_ID="<issue-219@loom.example.test>",
                                            Date="Mon, 02 Nov 2026 09:00:00 +0000"))
    app.post("/api/briefing/since", {"since": 0})
    [req] = ai.requests
    assert "The Weekly Loom" in req.prompt
    assert "CONGRATULATIONS" not in req.prompt


def test_hwf_since_is_held_between_a_day_ago_and_now(app, world, monkeypatch):
    from mailmind.bootstrap import assistant
    seen = []
    monkeypatch.setattr(assistant.briefs, "build_delta",
                        lambda accts, since, generate=True: (seen.append(since), {"ok": True, "empty": True, "count": 0})[1])
    app.clock.shift(timedelta(minutes=7))                    # 12:07
    now = int(time.time())
    for since in (0, now - 3600, now + 3600, "soon"):
        app.get(f"/api/briefing/since?since={since}")
        app.post("/api/briefing/since", {"since": since})
    day_ago = int(datetime(2026, 11, 1, 12, 0, tzinfo=UTC).timestamp())     # 12:07 a day back, to 10 minutes
    assert seen == [day_ago, day_ago, now - 3600, now - 3600, now, now, day_ago, day_ago]
