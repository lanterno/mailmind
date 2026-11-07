"""The one HTTP handler: the perimeter, then the route table. The app hands it each request through a HandlerBridge."""
import json
import secrets
import urllib.parse
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from mailmind.assistant.api import allowing_over
from mailmind.http import shared
from mailmind.http.routing import ANSWERED, Request, Routes
from mailmind.http.shared import AI_OVER_HEADER
from mailmind.http.shared import STATUS as _STATUS      # noqa: F401
from mailmind.http.shared import UI_QUIET as _UI_QUIET  # noqa: F401
from mailmind.platform import perimeter, settings
from mailmind.platform.logging import for_request

# What a route reads as text under these names, wherever it reads them: anything else here is a malformed request.
_TEXT_FIELDS = ("account", "email", "thread_id", "from_addr", "token", "decision", "mode", "subject", "text", "undo_group",
                "idempotency_key")


class H(BaseHTTPRequestHandler):
    CSRF = secrets.token_urlsafe(32)          # per-process; required on every mutating POST
    routes = Routes()                         # mailmind.bootstrap.web binds them

    def log_message(self, *a):
        pass

    def handle_one_request(self):
        """One request: what's logged while it runs carries its id."""
        with for_request():
            super().handle_one_request()

    def send_response_only(self, code, message=None):
        self._status, self._named = code, set()
        super().send_response_only(code, message)

    def send_header(self, keyword, value):
        self._named.add(keyword.lower())
        super().send_header(keyword, value)

    def end_headers(self):
        """Every answer, an error http.server writes itself included, carries the perimeter's
        headers; the ones a route set itself stand."""
        if self._status >= 200:
            for k, v in perimeter.answer_headers(urllib.parse.urlsplit(getattr(self, "path", "")).path):
                if k.lower() not in self._named:
                    self.send_header(k, v)
        super().end_headers()

    def _send(self, code, body, ctype, headers=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _send_raw(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # belt-and-braces: even if a browser ignores Content-Disposition, sandbox neuters it
        self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        """The request's JSON object, {} for no body, or None for a body that isn't a JSON object (an array, a string, not
        JSON at all) or that holds anything but text in a text field, which the route answers 400 once the guards pass."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            b = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except Exception:
            return None
        if not isinstance(b, dict) or any(not isinstance(b.get(name), str | None) for name in _TEXT_FIELDS):
            return None
        return b

    def _route(self, method, req):
        """Answer `req` with the route the table names, or 404."""
        route = self.routes.find(method, req.path)
        if route is None:
            return self._send(404, b"not found", "text/plain")
        answer = route(req)
        if answer is ANSWERED:
            return
        if isinstance(answer, tuple):
            return self._json(*answer)
        self._json(answer)

    def do_GET(self):
        no = perimeter.refuse_host(self.headers, settings.current().port)
        if no:
            return self._json(no.answer(), no.status)
        u = urlparse(self.path)
        no = perimeter.refuse_cross_site(self.headers, u.path)
        if no:
            return self._json(no.answer(), no.status)
        q = parse_qs(u.query)
        shared.touched(u.path)
        with allowing_over(self.headers.get(AI_OVER_HEADER) == "allow"):
            try:
                self._route("GET", Request(self, u.path, q))
            except Exception as e:
                self._json(*shared.failure_answer(e, "GET", u.path))

    def do_POST(self):
        u = urlparse(self.path)
        # 1) Cap the body size BEFORE reading it, so an unauthenticated/cross-tab
        #    request can't make us read+parse an arbitrary-size payload.
        no = perimeter.refuse_body(self.headers, shared.post_body_cap(u.path))
        if no:
            self.close_connection = True
            return self._json(no.answer(), no.status)
        b = self._body()                  # now safe to consume the request body
        # 2) Then gate every mutation: host, CSRF token, and on the send routes a matching Origin.
        no = perimeter.refuse_mutation(u.path, self.headers, port=settings.current().port, token=self.CSRF)
        if no:
            return self._json(no.answer(), no.status)
        if b is None:
            return self._json(shared.BAD_REQUEST, 400)
        shared.touched(u.path)            # only a request that got past the guards: someone is here
        with allowing_over(self.headers.get(AI_OVER_HEADER) == "allow"):
            try:
                self._route("POST", Request(self, u.path, {}, b))
            except Exception as e:
                self._json(*shared.failure_answer(e, "POST", u.path))


def bind(routes, someone_here):
    """Give the handler its table, and say what's told when someone uses the app."""
    H.routes = routes
    shared.tell_of_someone(someone_here)
