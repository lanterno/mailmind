"""mailmind.http.bridge.HandlerBridge: an http.server handler class served over ASGI, the way the
new front door serves the legacy app. A toy handler stands in for server.H."""
import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler

import pytest

from asgi_server import Uvicorn
from mailmind.http.bridge import HandlerBridge

CAP = 1000
SMALL = 10                          # the cap on /small, a route that takes less than the rest


asked = []                          # what the bridge asked body_cap about


def body_cap(method, path):
    """Toy reads a POST's body up to its route's cap, and no other body."""
    asked.append((method, path))
    return 0 if method != "POST" else SMALL if path == "/small" else CAP


class Toy(BaseHTTPRequestHandler):
    seen = []                       # what each request looked like to the handler
    proceed = threading.Event()     # lets a streaming response go on
    outcome = []                    # how a streaming response ended

    def log_message(self, *a):
        pass

    def _reply(self, code, obj, extra=()):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        Toy.seen.append({"path": self.path, "host": self.headers.get("Host"),
                         "tags": self.headers.get_all("X-Tag"), "client": self.client_address[0]})
        if self.path == "/boom":
            raise RuntimeError("secret detail")
        if self.path == "/silent":
            return
        if self.path == "/stream":
            return self._stream()
        if self.path == "/slow":
            Toy.proceed.wait(10)
            return self._reply(200, {"answered": True})
        if self.path == "/thread":
            t = threading.current_thread()
            Toy.outcome.append((t.name, t.daemon, t is threading.main_thread()))
        self._reply(200, {"ok": True}, [("X-Frame-Options", "DENY"), ("Connection", "keep-alive")])

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > (SMALL if self.path == "/small" else CAP):
            return self._reply(413, {"error": "payload too large"})
        body = self.rfile.read(length)
        Toy.seen.append({"path": self.path, "body": body.decode()})
        self._reply(200, {"got": len(body)})

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            self.wfile.write(b"first\n")
            Toy.proceed.wait(10)
            for _ in range(500):                        # up to 5 s, until the client's absence shows
                self.wfile.write(b"more\n")
                time.sleep(0.01)
            Toy.outcome.append("wrote everything")
        except BrokenPipeError:
            Toy.outcome.append("client gone")


@pytest.fixture
def served():
    Toy.seen, Toy.outcome = [], []
    asked.clear()
    Toy.proceed = threading.Event()
    srv = Uvicorn(HandlerBridge(Toy, body_cap=body_cap), name="bridge-http").start()
    try:
        yield srv
    finally:
        Toy.proceed.set()
        srv.stop()


def call(srv, method, path, body=None, headers=None, **kw):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    try:
        conn.request(method, path, body=body, headers={"Host": f"127.0.0.1:{srv.port}", **(headers or {})}, **kw)
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        conn.close()


def test_the_handler_sees_the_request_as_it_was_sent(served):
    conn = http.client.HTTPConnection("127.0.0.1", served.port, timeout=10)
    conn.putrequest("GET", "/api/x%2Fy?q=a%20b&n=1", skip_host=True)
    conn.putheader("Host", f"localhost:{served.port}")
    conn.putheader("X-Tag", "one")
    conn.putheader("X-Tag", "two")
    conn.endheaders()
    r = conn.getresponse()
    r.read()
    conn.close()
    assert Toy.seen == [{"path": "/api/x%2Fy?q=a%20b&n=1", "host": f"localhost:{served.port}",
                         "tags": ["one", "two"], "client": "127.0.0.1"}]


def test_the_handlers_answer_comes_back_without_its_connection_headers(served):
    status, headers, body = call(served, "GET", "/")
    assert (status, json.loads(body)) == (200, {"ok": True})
    assert headers["content-type"] == "application/json"
    assert headers["x-frame-options"] == "DENY"
    assert headers["server"].startswith("BaseHTTP/")             # the handler's own, as before
    assert headers.get("connection") != "keep-alive"


def test_a_post_body_reaches_the_handler(served):
    status, _, body = call(served, "POST", "/p", body=b"x" * 600, headers={"Content-Type": "text/plain"})
    assert (status, json.loads(body)) == (200, {"got": 600})
    assert Toy.seen == [{"path": "/p", "body": "x" * 600}]


def test_a_body_declared_over_the_cap_is_left_unread_for_the_handler_to_refuse(served):
    status, _, body = call(served, "POST", "/p", body=b"x" * (CAP + 1))
    assert (status, json.loads(body)) == (413, {"error": "payload too large"})
    assert Toy.seen == []


def test_a_body_declared_over_its_routes_cap_is_refused_without_waiting_for_it(served):
    """The handler refuses on the declared length alone, so the bridge mustn't wait for bytes that
    may never come, even where another route would take that many."""
    conn = http.client.HTTPConnection("127.0.0.1", served.port, timeout=5)
    try:
        conn.putrequest("POST", "/small", skip_host=True)
        conn.putheader("Host", f"127.0.0.1:{served.port}")
        conn.putheader("Content-Length", str(SMALL + 1))
        conn.endheaders()
        conn.send(b"{}")
        r = conn.getresponse()
        assert (r.status, json.loads(r.read())) == (413, {"error": "payload too large"})
    finally:
        conn.close()


def test_the_cap_is_asked_per_request_with_the_path_as_sent(served):
    call(served, "POST", "/a%2Fb?x=1", body=b"{}")
    call(served, "GET", "/c%20d")
    assert asked == [("POST", "/a%2Fb"), ("GET", "/c%20d")]


def test_a_body_the_handler_never_reads_is_not_waited_for(served):
    conn = http.client.HTTPConnection("127.0.0.1", served.port, timeout=5)
    try:
        conn.putrequest("GET", "/", skip_host=True)
        conn.putheader("Host", f"127.0.0.1:{served.port}")
        conn.putheader("Content-Length", "5")
        conn.endheaders()
        r = conn.getresponse()
        assert r.status == 200
    finally:
        conn.close()


def test_an_undeclared_body_over_the_cap_is_refused_before_the_handler_runs(served):
    chunks = iter([b"x" * 600, b"x" * 600])
    status, _, body = call(served, "POST", "/p", body=chunks, encode_chunked=True,
                           headers={"Transfer-Encoding": "chunked"})
    assert (status, body) == (413, b"payload too large")
    assert Toy.seen == []


def test_a_response_streams_while_the_handler_is_still_writing(served):
    conn = http.client.HTTPConnection("127.0.0.1", served.port, timeout=10)
    conn.request("GET", "/stream", headers={"Host": f"127.0.0.1:{served.port}"})
    r = conn.getresponse()
    assert r.status == 200
    assert r.readline() == b"first\n"                       # before the handler may go on
    Toy.proceed.set()
    assert r.readline() == b"more\n"
    conn.close()


def test_the_handler_learns_the_client_left_from_its_next_write(served):
    conn = http.client.HTTPConnection("127.0.0.1", served.port, timeout=10)
    conn.request("GET", "/stream", headers={"Host": f"127.0.0.1:{served.port}"})
    r = conn.getresponse()
    assert r.readline() == b"first\n"
    r.close()
    conn.close()
    Toy.proceed.set()
    served.stop()                                           # waits for the handler's thread
    assert Toy.outcome == ["client gone"]


@pytest.mark.parametrize("path", ["/boom", "/silent"])
def test_a_handler_that_fails_or_answers_nothing_gives_a_bare_500(served, path):
    status, headers, body = call(served, "GET", path)
    assert (status, body) == (500, b"request failed")
    assert headers["content-type"] == "text/plain; charset=utf-8"


def test_a_method_the_handler_lacks_gets_its_own_501(served):
    status, _, _ = call(served, "DELETE", "/")
    assert status == 501


def test_a_stream_ends_when_the_server_shuts_down_under_it():
    """uvicorn cancels what is still running once its grace period is over; the handler's thread
    can't be cancelled, so its next write fails instead of keeping the stream, and the shutdown,
    alive."""
    Toy.seen, Toy.outcome = [], []
    Toy.proceed = threading.Event()
    srv = Uvicorn(HandlerBridge(Toy, body_cap=body_cap), name="bridge-http", grace=0.2).start()
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    try:
        conn.request("GET", "/stream", headers={"Host": f"127.0.0.1:{srv.port}"})
        r = conn.getresponse()
        assert r.readline() == b"first\n"
        Toy.proceed.set()
        began = time.monotonic()
        srv.stop()
        assert time.monotonic() - began < 3
        deadline = time.monotonic() + 3
        while not Toy.outcome and time.monotonic() < deadline:
            time.sleep(0.01)
        assert Toy.outcome == ["client gone"]
    finally:
        conn.close()


def test_each_request_runs_on_a_daemon_thread_of_its_own(served):
    """As under ThreadingHTTPServer: a handler still blocked when the process exits doesn't hold
    the exit, and no request waits for another's thread."""
    call(served, "GET", "/thread")
    call(served, "GET", "/thread")
    assert Toy.outcome == [("bridge-request", True, False)] * 2


@pytest.fixture
def closing():
    """The bridge on a server that calls its close() as soon as shutdown begins, with a grace
    period long enough to notice if anything waits it out."""
    Toy.seen, Toy.outcome = [], []
    Toy.proceed = threading.Event()
    bridge = HandlerBridge(Toy, body_cap=body_cap)
    srv = Uvicorn(bridge, name="bridge-http", grace=5, before_shutdown=bridge.close).start()
    yield srv
    Toy.proceed.set()
    if srv.thread.is_alive():
        srv.stop()


def test_shutting_down_ends_an_open_stream_at_once(closing):
    conn = http.client.HTTPConnection("127.0.0.1", closing.port, timeout=10)
    try:
        conn.request("GET", "/stream", headers={"Host": f"127.0.0.1:{closing.port}"})
        r = conn.getresponse()
        assert r.readline() == b"first\n"                   # the handler now waits, as between heartbeats
        began = time.monotonic()
        closing.stop()
        assert time.monotonic() - began < 2
        assert r.read() == b""                              # ended cleanly, not cut off
    finally:
        conn.close()
    Toy.proceed.set()
    deadline = time.monotonic() + 3
    while not Toy.outcome and time.monotonic() < deadline:
        time.sleep(0.01)
    assert Toy.outcome == ["client gone"]


def test_shutting_down_lets_a_request_still_being_answered_finish(closing):
    got = []
    t = threading.Thread(target=lambda: got.append(call(closing, "GET", "/slow")))
    t.start()
    deadline = time.monotonic() + 3
    while not Toy.seen and time.monotonic() < deadline:     # the handler is waiting to answer
        time.sleep(0.01)
    threading.Timer(0.3, Toy.proceed.set).start()
    closing.stop()
    t.join(5)
    status, _, body = got[0]
    assert (status, json.loads(body)) == (200, {"answered": True})
