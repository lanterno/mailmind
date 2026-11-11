"""What holds background AI back: an interactive call running, and which requests count as someone at the screen."""
import socket
import time
from urllib.parse import urlencode

from fixtures import world as w
from mailmind.bootstrap import assistant
from waiting import wait_until
from world_helpers import VENUE_END


def test_hwf_what_a_hidden_tab_polls_for_its_meeting_reminder_doesnt_count_as_someone_here(app, monkeypatch):
    seen = []
    monkeypatch.setattr(assistant.governor(), "saw_someone", lambda: seen.append(1))
    app.get("/api/upcoming")
    assert seen == []
    app.get("/api/digest")
    assert seen


def test_hwf_polling_routes_dont_count_as_someone_at_the_screen(app, world, monkeypatch):
    from mailmind.bootstrap import assistant
    from mailmind.platform import sse
    governor = assistant.governor()
    monkeypatch.setattr(sse, "HEARTBEAT", 0.1)
    monkeypatch.setattr(governor, "_last_seen", 0.0)
    for path in (f"/api/folders?account={w.ADA}", "/api/outbox", "/api/stats",
                 "/api/ai/draft-ahead?" + urlencode(world.copy(VENUE_END))):
        assert app.get(path).status == 200
    s = socket.create_connection(("127.0.0.1", app.port), timeout=10)     # a hidden tab's event stream
    s.sendall(f"GET /api/events HTTP/1.1\r\nHost: 127.0.0.1:{app.port}\r\nSec-Fetch-Site: same-origin\r\n\r\n"
              .encode())
    assert b" 200 " in s.recv(64)
    s.close()
    assert wait_until(lambda: not sse.HUB.listening())                 # its stream has ended
    assert governor._last_seen == 0.0
    app.get("/api/digest")
    assert governor._last_seen == time.time()


def test_hwf_background_ai_waits_while_an_interactive_call_runs():
    from mailmind.assistant.application.governor import CostGovernor
    governor = CostGovernor(spent=lambda: 0, limit=lambda: 1)
    assert governor.held_back("background", "triage", "m", None) is None
    with governor.call("interactive", "rewrite", "m", None):              # one call in flight
        assert governor.held_back("background", "triage", "m", None) == "yielding"
