"""The app's HTTP server, run in-process against the test's world, and a client that talks to it
the way the page does: same-origin, with the CSRF token on every request.

Each test gets a clean server module: `reset_state` puts every module's private globals back to
how they were at import (caches, the undo stack, the sync status, presence timestamps), so no
test sees another's leftovers.
"""
import http.client
import importlib
import json
import os
import threading
import time
from dataclasses import dataclass

CSRF = "test-csrf-token"

# Modules whose private globals hold process state between requests, and instances (`module:name`)
# whose private attributes do.
STATEFUL = ("server", "mailmind.bootstrap.assistant:calls", 
            "mailmind.bootstrap.mailbox:pool", "mailmind.bootstrap.mailbox:warm", "mailmind.bootstrap.mailbox:realtime", "mailmind.bootstrap.mailbox:stats", "mailmind.bootstrap.mailbox:folders", "mailmind.bootstrap.mailbox:syncing",
            "mailmind.curation.application.unsubscribe")
_SIMPLE = (dict, list, set, int, float, str, bool, type(None))
_pristine = {}


def _holder(name):
    module, _, attr = name.partition(":")
    mod = importlib.import_module(module)
    return getattr(mod, attr) if attr else mod


def snapshot():
    """Record each stateful module's private globals once, as they were at import."""
    for name in STATEFUL:
        mod = _holder(name)
        if name in _pristine:
            continue
        saved = {}
        for k, v in vars(mod).items():
            if k.startswith("_") and not k.startswith("__") and isinstance(v, _SIMPLE):
                saved[k] = v.copy() if isinstance(v, (dict, list, set)) else v
        _pristine[name] = saved


def reset_state():
    """Put every recorded global back. Containers are refilled in place, so a module that holds
    a reference to one keeps a live reference."""
    snapshot()
    for name, saved in _pristine.items():
        mod = _holder(name)
        for k, v in saved.items():
            cur = getattr(mod, k, None)
            if isinstance(v, dict) and isinstance(cur, dict):
                cur.clear()
                cur.update(v)
            elif isinstance(v, list) and isinstance(cur, list):
                cur[:] = v
            elif isinstance(v, set) and isinstance(cur, set):
                cur.clear()
                cur.update(v)
            else:
                setattr(mod, k, v)
    from mailmind.bootstrap import assistant, legacy, mailbox
    from mailmind.platform import sse
    legacy.unwire()                          # the server's subscriptions on the bus, as wire() made them
    sse.HUB = sse.Hub()                      # no events kept, no tab listening, a run of its own
    assistant.use("urllib")                  # the old door's client; the new door's build picks the SDK
    mailbox.reset()                          # imaplib alone; the new door's build offers IMAPClient
    assistant.governor().forget()            # a restart: no interactive call lately, someone here now


RECORD_API = os.environ.get("MAILMIND_RECORD_API")     # a file to note each JSON answer in, for `just gen-api`


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
        """Threads alive now that weren't when the client was made, other than the fakes' and
        the app's serving thread: work a request started in the background (a sync, a draft)."""
        return [t for t in threading.enumerate()
                if t not in self._before and t is not threading.current_thread()
                and not t.name.startswith(("fake-", "app-http"))]

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
        if hdrs.get("content-type", "").startswith("application/json"):
            parsed = json.loads(payload)
        if RECORD_API and 200 <= r.status < 300 and isinstance(parsed, (dict, list)):
            with open(RECORD_API, "a", encoding="utf-8") as f:       # for tests/gen_api_types.py
                f.write(json.dumps([method, path, parsed]) + "\n")
        return Response(r.status, hdrs, parsed)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body, **kw)


class RunningApp:
    """The old front door: server.H on ThreadingHTTPServer, on a random loopback port, serving
    from a background thread."""

    def __init__(self):
        import server
        self.httpd = server._Server(("127.0.0.1", 0), server.H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05},
                                       name="app-http", daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class RunningAsgiApp:
    """The new front door: server.H behind FastAPI and uvicorn, as `python -m mailmind serve`
    builds it, on a random loopback port, serving from a background thread."""

    def __init__(self):
        import server
        from asgi_server import Uvicorn
        from mailmind.bootstrap import front
        app, before_shutdown = front.build(server)
        self.uvicorn = Uvicorn(app, before_shutdown=before_shutdown)
        self.port = self.uvicorn.port

    def start(self):
        self.uvicorn.start()
        return self

    def stop(self):
        self.uvicorn.stop()
