"""The live event stream, /api/events: what a listener receives, in the server-sent-events framing,
and how the stream ends. The golden files are in tests/golden/http_events/.

The stream is read over a raw socket, as EventSource would: a GET without the CSRF header, which
EventSource can't send. The keepalive interval is cut to a fraction of a second, so a closed
listener is noticed (and its thread ends) within the test.
"""
import socket
import time

import pytest

from fixtures import world as w


class Stream:
    def __init__(self, app, **headers):
        self.sock = socket.create_connection(("127.0.0.1", app.port), timeout=10)
        h = {"Host": f"127.0.0.1:{app.port}", "Accept": "text/event-stream", "Sec-Fetch-Site": "same-origin",
             **headers}
        self.sock.sendall(("GET /api/events HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items())
                           + "\r\n").encode())
        self.f = self.sock.makefile("rb")
        self.status = int(self.f.readline().split()[1])
        self.headers = {}
        while (line := self.f.readline().decode().rstrip("\r\n")):
            k, _, v = line.partition(":")
            self.headers[k.lower()] = v.strip()

    def frame(self):
        """The next frame, without the blank line that ends it."""
        lines = []
        while (line := self.f.readline().decode()) not in ("\n", ""):
            lines.append(line.rstrip("\n"))
        return "\n".join(lines)

    def close(self):
        self.f.close()
        self.sock.close()


@pytest.fixture
def quick(monkeypatch):
    import realtime
    monkeypatch.setattr(realtime, "HEARTBEAT", 0.2)
    return realtime


def subscribers(realtime, n, timeout=5):
    deadline = time.monotonic() + timeout
    while len(realtime._subs) != n and time.monotonic() < deadline:
        time.sleep(0.02)
    return len(realtime._subs)


def test_a_listener_hears_each_event_then_a_keepalive(app, golden, world, quick):
    s = Stream(app)
    golden.note("status and headers", {"status": s.status, "headers": {k: v for k, v in s.headers.items()
                                                                       if k not in ("date", "server")}})
    golden.note("first frame", s.frame())
    lunch = world.row("ada-inbox-02-lunch-confirm.eml")
    app.post("/api/mute", {"pk": lunch["pk"]})
    app.post("/api/undo", {})
    app.post("/api/replylater", {"pk": lunch["pk"]})
    golden.note("frames for mute, undo and Reply Later", [s.frame() for _ in range(3)])
    golden.note("then, with nothing to say", s.frame())
    s.close()
    golden.note("listeners once it hangs up", subscribers(quick, 0))


def test_every_listener_hears_an_event(app, golden, world, quick):
    a, b = Stream(app), Stream(app)
    for s in (a, b):
        s.frame()
    app.post("/api/mute", {"account": w.CY, "thread_id": world.row("cy-inbox-01-thread-start.eml")["thread_id"]})
    golden.note("what each heard", [a.frame(), b.frame()])
    for s in (a, b):
        s.close()
    golden.note("listeners once both hang up", subscribers(quick, 0))


def test_a_listener_past_the_cap_is_turned_away(app, golden, quick, monkeypatch):
    monkeypatch.setattr(quick, "MAX_SUBS", 1)
    s = Stream(app)
    s.frame()
    golden.record("a second listener", app.get("/api/events"))
    s.close()
    subscribers(quick, 0)
    t = Stream(app)
    golden.note("a listener after the first hung up", {"status": t.status, "first frame": t.frame()})
    t.close()
    subscribers(quick, 0)


def test_a_cross_site_listener_is_refused(app, golden, quick):
    golden.record("cross-site", app.get("/api/events", headers={"Sec-Fetch-Site": "cross-site"}))
    golden.record("another host", app.get("/api/events", headers={"Host": "mail.example.test"}))
    golden.note("listeners", len(quick._subs))
