"""The live event stream, /api/events: what a listener receives, in the server-sent-events framing,
how a tab that reconnects resumes, and how the stream ends. The golden files are in
tests/golden/http_events/, each event's id written <run>-<n>.

The stream is read over a raw socket, as EventSource would: a GET without the CSRF header, which
EventSource can't send. Its body may run until the connection closes (the old front door's
HTTP/1.0 answer) or come in chunks (uvicorn's HTTP/1.1); either reads the same, and the headers
that only frame the connection are left out of the record. The keepalive interval is cut to a fraction of a second, so a closed
listener is noticed (and its thread ends) within the test.
"""
import socket
import time

# Headers about the connection, not the stream: each front door frames it its own way.
CONNECTION_HEADERS = {"connection", "keep-alive", "transfer-encoding", "date", "server"}

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
        self.body = _Chunked(self.f) if self.headers.get("transfer-encoding") == "chunked" else self.f

    def frame(self):
        """The next frame, without the blank line that ends it."""
        lines = []
        while (line := self.body.readline().decode()) not in ("\n", ""):
            lines.append(line.rstrip("\n"))
        return "\n".join(lines)

    def event(self):
        """The next frame that isn't a keepalive: work that outlasts the cut keepalive interval
        lets one through first."""
        while (frame := self.frame()) == ": keepalive":
            pass
        return frame

    def close(self):
        self.f.close()
        self.sock.close()


class _Chunked:
    """A chunked body, read a line at a time."""

    def __init__(self, f):
        self.f = f
        self.buf = b""

    def readline(self):
        while b"\n" not in self.buf:
            size = self.f.readline().split(b";")[0].strip()
            if not size or int(size, 16) == 0:
                break
            self.buf += self.f.read(int(size, 16))
            self.f.readline()                           # the CRLF after each chunk
        line, sep, self.buf = self.buf.partition(b"\n")
        return line + sep


@pytest.fixture
def quick(monkeypatch):
    """The stream's hub, with the keepalive cut to a fifth of a second."""
    from mailmind.platform import sse
    monkeypatch.setattr(sse, "HEARTBEAT", 0.2)
    return sse


def subscribers(sse, n, timeout=5):
    deadline = time.monotonic() + timeout
    while sse.HUB.listening() != n and time.monotonic() < deadline:
        time.sleep(0.02)
    return sse.HUB.listening()


def test_a_listener_hears_each_event_then_a_keepalive(app, golden, world, quick):
    s = Stream(app)
    golden.note("status and headers", {"status": s.status, "headers": {k: v for k, v in s.headers.items()
                                                                       if k not in CONNECTION_HEADERS}})
    golden.note("first frame", s.frame())
    lunch = world.row("ada-inbox-02-lunch-confirm.eml")
    app.post("/api/mute", {"pk": lunch["pk"]})
    app.post("/api/undo", {})
    app.post("/api/replylater", {"pk": lunch["pk"]})
    golden.note("frames for mute, undo and Reply Later", [s.event() for _ in range(3)])
    golden.note("then, with nothing to say", s.frame())
    s.close()
    golden.note("listeners once it hangs up", subscribers(quick, 0))


def test_every_listener_hears_an_event(app, golden, world, quick):
    a, b = Stream(app), Stream(app)
    for s in (a, b):
        s.frame()
    app.post("/api/mute", {"account": w.CY, "thread_id": world.row("cy-inbox-01-thread-start.eml")["thread_id"]})
    golden.note("what each heard", [a.event(), b.event()])
    for s in (a, b):
        s.close()
    golden.note("listeners once both hang up", subscribers(quick, 0))


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


def test_a_cross_site_listener_is_refused(app, golden, quick):
    golden.record("cross-site", app.get("/api/events", headers={"Sec-Fetch-Site": "cross-site"}))
    golden.record("another host", app.get("/api/events", headers={"Host": "mail.example.test"}))
    golden.note("listeners", quick.HUB.listening())


# -- a tab that reconnects ------------------------------------------------------

def test_a_tab_that_reconnects_hears_what_it_missed(app, golden, world, quick):
    lunch = world.row("ada-inbox-02-lunch-confirm.eml")
    s = Stream(app)
    golden.note("first frame", s.frame())
    app.post("/api/mute", {"pk": lunch["pk"]})
    heard = s.event()
    s.close()                                                     # the tab drops, say a laptop lid
    subscribers(quick, 0)
    app.post("/api/undo", {})
    app.post("/api/replylater", {"pk": lunch["pk"]})
    last = heard.split("\n")[0].removeprefix("id: ")
    t = Stream(app, **{"Last-Event-ID": last})
    golden.note("after the mute, it reconnects and hears", [t.frame() for _ in range(3)])
    golden.note("then, with nothing to say", t.frame())
    t.close()
    subscribers(quick, 0)


def test_a_tab_that_never_heard_an_event_resumes_from_where_it_opened(app, golden, world, quick):
    s = Stream(app)
    opened = s.frame()
    s.close()
    subscribers(quick, 0)
    app.post("/api/mute", {"pk": world.pk("ada-inbox-02-lunch-confirm.eml")})
    t = Stream(app, **{"Last-Event-ID": opened.split("\nid: ")[1].split("\n")[0]})
    golden.note("it opened with", opened)
    golden.note("and on reconnecting hears", [t.frame(), t.frame()])
    t.close()
    subscribers(quick, 0)


def test_a_tab_from_before_a_restart_is_told_to_fetch_afresh(app, golden, world, quick, monkeypatch):
    s = Stream(app)
    s.frame()
    app.post("/api/mute", {"pk": world.pk("ada-inbox-02-lunch-confirm.eml")})
    last = s.event().split("\n")[0].removeprefix("id: ")
    s.close()
    subscribers(quick, 0)
    monkeypatch.setattr(quick, "HUB", quick.Hub(run="later"))   # the process restarted
    t = Stream(app, **{"Last-Event-ID": last})
    golden.note("it reconnects and hears", [t.frame(), t.frame()])
    t.close()
    subscribers(quick, 0)


def test_a_tab_further_back_than_the_hub_keeps_is_told_to_fetch_afresh(app, golden, world, quick, monkeypatch):
    monkeypatch.setattr(quick, "HUB", quick.Hub(ring=2, run="small"))
    lunch = world.row("ada-inbox-02-lunch-confirm.eml")
    for _ in range(2):
        app.post("/api/mute", {"pk": lunch["pk"]})
        app.post("/api/undo", {})
    t = Stream(app, **{"Last-Event-ID": "small-1"})
    golden.note("it reconnects and hears", [t.frame(), t.frame()])
    t.close()
    subscribers(quick, 0)


def test_a_send_that_goes_out_tells_the_open_tabs(app, world, quick):
    from datetime import timedelta
    import outbox
    s = Stream(app)
    s.frame()
    app.post("/api/send", {"account": w.ADA, "mode": "new", "to": ["lin@example.test"], "subject": "Linen",
                           "text": "Half is yours.", "idempotency_key": "k-tabs"})
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    assert s.event().split("\n")[1:] == ["event: outbox", "data: {}"]
    s.close()
    subscribers(quick, 0)


def test_a_queued_move_the_server_refuses_tells_the_open_tabs(app, world, quick):
    import readmutq
    s = Stream(app)
    s.frame()
    world.server.drop("SELECT", user=w.ADA)
    assert app.post("/api/archive", {"pk": world.pk("ada-inbox-05-receipt.eml"),
                                     "mutation_id": "m-no"}).json["status"] == "queued"
    world.server.fail("UID MOVE", user=w.ADA)
    world.server.fail("UID COPY", user=w.ADA)
    readmutq.drain()
    assert s.event().split("\n")[1:] == ["event: commands-changed", "data: {}"]
    s.close()
    subscribers(quick, 0)


def test_a_queued_move_that_lands_tells_the_open_tabs(app, world, quick):
    import readmutq
    s = Stream(app)
    s.frame()
    world.server.drop("SELECT", user=w.ADA)
    r = app.post("/api/archive", {"pk": world.pk("ada-inbox-05-receipt.eml"), "mutation_id": "m-tabs"}).json
    assert r["status"] == "queued"
    readmutq.drain()
    assert s.event().split("\n")[1:] == ["event: commands-changed", "data: {}"]
    s.close()
    subscribers(quick, 0)
