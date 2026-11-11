"""The app's HTTP server, run in-process against the test's world, and a client that talks to it
the way the page does: same-origin, with the CSRF token on every request.

Each test gets a new app: conftest's `_fresh_app` assembles every context's objects afresh
(mailmind.bootstrap.wiring.assemble) before it starts, so no test sees another's caches, undo stack,
sync status or presence, and none has to say which module keeps what.
"""
import http.client
import json
import threading
import time
from dataclasses import dataclass

CSRF = "test-csrf-token"


def restart():
    """The process restarts while the test goes on: everything held in memory made afresh and wired as the
    server wires it, and only the database remembers."""
    from mailmind.bootstrap import wiring
    wiring.assemble()
    wiring.wire()


@dataclass
class Response:
    status: int
    headers: dict
    body: object            # parsed JSON, or bytes for anything else

    @property
    def json(self):
        assert isinstance(self.body, (dict, list)), f"not JSON: {self.headers.get('content-type')}"
        return self.body


class AppClient:
    def __init__(self, port):
        self.port = port
        self.origin = f"http://127.0.0.1:{port}"
        self._before = set(threading.enumerate())

    def started_threads(self):
        """Threads alive now that weren't when the client was made, other than the fakes', the
        app's serving thread and the pool the routes run on, whose threads wait for the next
        request: work a request started in the background (a sync, a draft)."""
        return [t for t in threading.enumerate()
                if t not in self._before and t is not threading.current_thread()
                and not t.name.startswith(("fake-", "app-http", "AnyIO worker"))]

    def settle(self, timeout=30):
        """Wait for the background work requests started. Raises if any is still running, so a
        test never ends with a thread that would write into the next test's data directory."""
        deadline = time.monotonic() + timeout
        for t in self.started_threads():
            t.join(max(0.0, deadline - time.monotonic()))
        left = [t.name for t in self.started_threads() if t.is_alive()]
        if left:
            raise AssertionError(f"background threads still running after {timeout}s: {left}")

    def request(self, method, path, body=None, headers=None):
        h = {"Host": f"127.0.0.1:{self.port}", "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": CSRF}
        data = None
        if method == "POST":
            h["Origin"] = self.origin
            h["Content-Type"] = "application/json"
            data = body if isinstance(body, bytes) else json.dumps(body if body is not None else {}).encode()
        for k, v in (headers or {}).items():
            if v is None:
                h.pop(k, None)
            else:
                h[k] = v
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            conn.request(method, path, body=data, headers=h)
            r = conn.getresponse()
            payload = r.read()
            hdrs = {k.lower(): v for k, v in r.getheaders()}
        finally:
            conn.close()
        parsed = payload
        if payload and hdrs.get("content-type", "").startswith(("application/json", "application/problem+json")):   # a HEAD has none
            parsed = json.loads(payload)
        return Response(r.status, hdrs, parsed)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body, **kw)


class RunningAsgiApp:
    """The app, as `python -m mailmind serve` builds it, on a random loopback port, serving from a
    background thread, with what its routes answer held to their schemas."""

    def __init__(self):
        import schema_contract
        from asgi_server import Uvicorn
        from mailmind.bootstrap import front
        self.asgi = front.build()
        self.uvicorn = Uvicorn(schema_contract.HoldingAnswers(self.asgi))
        self.port = self.uvicorn.port

    def start(self):
        self.uvicorn.start()
        return self

    def stop(self):
        self.uvicorn.stop()
