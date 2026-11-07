"""The one HTTP handler both front doors serve: the perimeter, then the route table.

`server.py` puts it on the standard library's server. The new door hands it each request through a HandlerBridge. Standard
library only: python3 server.py runs it with nothing installed.
"""
import json
import secrets
import urllib.parse
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from mailmind.assistant.api import AIOff, OverLimit, allowing_over
from mailmind.compose.api import MAX_SEND_POST
from mailmind.http.routing import ANSWERED, Request, Routes
from mailmind.kernel.errors import Busy, Conflict, Locked, NotFound, Refused, Unreachable, Unreadable
from mailmind.platform import perimeter, profile, settings
from mailmind.platform.logging import for_request, get_logger

log = get_logger("http")

MAX_POST = 1 << 20                        # 1 MB cap for control routes


def post_body_cap(path):
    """The most a POST to `path` may carry, in bytes, checked against its Content-Length before
    the body is read: 38 MB where mail and its attachments go up, 1 MB everywhere else."""
    return MAX_SEND_POST if path in ("/api/send", "/api/draft") else MAX_POST


_UI_QUIET = {"/api/events", "/api/folders", "/api/outbox", "/api/pending", "/api/stats",
             "/api/ai/draft-ahead",   # fetched by hidden tabs via SSE — incl. the draft-ready echo itself,
                                      # or the tick's own events would hold the vacation gate open forever
             "/api/debug/health"}     # asking how the app is doing changes nothing, this included


# What a route reads as text under these names, wherever it reads them: anything else here is a malformed request.
_TEXT_FIELDS = ("account", "email", "thread_id", "from_addr", "token", "decision", "mode", "subject", "text", "undo_group",
                "idempotency_key")

AI_OVER_HEADER = "X-MailMind-AI-Over-Limit"   # "allow": the user said this request may go over


# The status each answers with. 502 says asking again can help, and 422 that it won't.
_STATUS = {NotFound: 404, Conflict: 409, Locked: 423, Refused: 400, Unreachable: 502, Unreadable: 422, Busy: 409}
_PROBLEMS = tuple(_STATUS)


def problem_answer(e):
    """A Problem as both clients read one, {ok: false, error}, and its status. RFC 9457's form waits for the day / is the
    React app."""
    said = {"error": e.said} if e.said else {}
    return {"ok": False, **said, **e.more}, next(code for kind, code in _STATUS.items() if isinstance(e, kind))


def _over_limit_answer(e):
    """The 402 an interactive AI request gets at the daily limit. The page asks the user, then
    sends the request again with AI_OVER_HEADER."""
    return {"ok": False, "error": "today's AI spending has reached the daily limit", "over_limit": True,
            "spent_micros": e.spent_micros, "limit_micros": e.limit_micros}, 402


def _ai_off_answer():
    """The 409 an AI request gets with AI off. Nothing was sent, and asking again won't help."""
    return {"ok": False, "error": "AI is off: MailMind has no Claude API key", "ai_off": True}, 409


class H(BaseHTTPRequestHandler):
    CSRF = secrets.token_urlsafe(32)          # per-process; required on every mutating POST
    routes = Routes()                         # mailmind.bootstrap.web binds them, with someone_here
    someone_here = staticmethod(lambda: None)

    def log_message(self, *a):
        pass

    def handle_one_request(self):
        """One request, through either door: what's logged while it runs carries its id."""
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

    def _ui_touch(self, path):
        """Someone is here, for the cost governor's presence gate on drafting ahead. _UI_QUIET routes
        are excluded because the SSE followup/outbox listeners re-fetch them even from a hidden,
        forgotten-open tab."""
        if path.startswith("/api/") and path not in _UI_QUIET:
            self.someone_here()

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
        self._ui_touch(u.path)
        over = allowing_over(self.headers.get(AI_OVER_HEADER) == "allow")
        over.__enter__()
        try:
            self._route("GET", Request(self, u.path, q))
        except _PROBLEMS as e:
            self._json(*problem_answer(e))
        except OverLimit as e:      # the page asks before it goes over
            self._json(*_over_limit_answer(e))
        except AIOff:               # no key: nothing was sent, and nothing will be until one is set
            self._json(*_ai_off_answer())
        except Exception as e:                # scrub: log the detail, never echo it to the client
            log.error("GET %(path)s failed", {"path": u.path}, exc_info=e)
            self._json({"ok": False, "error": "request failed"}, 500)
        finally:
            over.__exit__(None, None, None)

    def do_POST(self):
        u = urlparse(self.path)
        # 1) Cap the body size BEFORE reading it, so an unauthenticated/cross-tab
        #    request can't make us read+parse an arbitrary-size payload.
        no = perimeter.refuse_body(self.headers, post_body_cap(u.path))
        if no:
            self.close_connection = True
            return self._json(no.answer(), no.status)
        b = self._body()                  # now safe to consume the request body
        # 2) Then gate every mutation: host, CSRF token, and on the send routes a matching Origin.
        no = perimeter.refuse_mutation(u.path, self.headers, port=settings.current().port, token=self.CSRF)
        if no:
            return self._json(no.answer(), no.status)
        if b is None:
            return self._json({"ok": False, "error": "bad request"}, 400)
        self._ui_touch(u.path)            # only a request that got past the guards: someone is here
        over = allowing_over(self.headers.get(AI_OVER_HEADER) == "allow")
        over.__enter__()
        try:
            self._route("POST", Request(self, u.path, {}, b))
        except _PROBLEMS as e:
            self._json(*problem_answer(e))
        except (TypeError, ValueError):       # malformed request input (bad/missing int id/pk) → clean 400
            self._json({"ok": False, "error": "bad request"}, 400)
        except profile.ReadOnlyError as e:    # a write this process may not make (and nothing queued it)
            log.info("POST %(path)s refused, read-only: %(what)s", {"path": u.path, "what": str(e)})
            self._json({"ok": False, "error": "MailMind is running read-only", "read_only": True}, 409)
        except OverLimit as e:      # the page asks before it goes over
            self._json(*_over_limit_answer(e))
        except AIOff:               # no key: nothing was sent, and nothing will be until one is set
            self._json(*_ai_off_answer())
        except Exception as e:                # scrub: log the detail, never echo it to the client
            log.error("POST %(path)s failed", {"path": u.path}, exc_info=e)
            self._json({"ok": False, "error": "request failed"}, 500)
        finally:
            over.__exit__(None, None, None)


def bind(routes, someone_here):
    """Give the handler its table, and what it tells when someone uses the app."""
    H.routes = routes
    H.someone_here = staticmethod(someone_here)
