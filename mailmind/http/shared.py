"""What a request to a route goes through (mailmind.http.route): what a failure answers, who counts as someone here, how far a
body may run. It imports nothing of FastAPI.
"""
import json
from http import HTTPStatus

from mailmind.assistant.api import AIOff, OverLimit
from mailmind.compose.api import MAX_SEND_POST
from mailmind.kernel.errors import Busy, Conflict, Locked, NotFound, Refused, Unreachable, Unreadable
from mailmind.platform import profile
from mailmind.platform.logging import get_logger

log = get_logger("http")

MAX_POST = 1 << 20                        # 1 MB cap for control routes

AI_OVER_HEADER = "X-MailMind-AI-Over-Limit"   # "allow": the user said this request may go over

UI_QUIET = {"/api/events", "/api/folders", "/api/outbox", "/api/pending", "/api/stats",
            "/api/upcoming",         # the meeting reminder reads it every minute, from a hidden tab too
            "/api/ai/draft-ahead",   # fetched by hidden tabs via SSE — incl. the draft-ready echo itself,
                                     # or the tick's own events would hold the vacation gate open forever
            "/api/debug/health",     # asking how the app is doing changes nothing, this included
            "/api/places"}           # a list a hidden tab draws again sends the places its links name

# Each of the kernel's, the status it answers with and the kind of problem it is. 502 says asking again can help, and 422 that it
# won't.
KINDS = {NotFound: (404, "not-found"), Conflict: (409, "conflict"), Locked: (423, "locked"), Refused: (400, "refused"),
         Unreachable: (502, "unreachable"), Unreadable: (422, "unreadable"), Busy: (409, "busy")}
STATUS = {kind: status for kind, (status, _) in KINDS.items()}
_PROBLEMS = tuple(KINDS)

# A failure answers a problem document (RFC 9457). Its `type` is `/problems/<kind>`, a URI relative to the app that names the kind
# and that no client fetches, or `about:blank` where the status says all there is: a literal that means none of the kinds, the
# perimeter's refusals among them.
PROBLEM_JSON = "application/problem+json"
_TITLES = {"not-found": "Not found", "conflict": "Conflict", "locked": "Locked", "refused": "Refused",
           "unreachable": "Unreachable", "unreadable": "Unreadable", "busy": "Busy",
           "over-limit": "Over the daily AI limit", "ai-off": "AI is off", "read-only": "Read-only"}
_MEMBERS = ("type", "title", "status", "detail")


def problem(status, detail="", kind=None, /, **more):
    """A problem document and its status. `detail` is what a person is told, left out where the page words it; `more` is what
    else the page acts on, as members of its own beside the four."""
    doc = {"type": f"/problems/{kind}", "title": _TITLES[kind]} if kind else {"type": "about:blank", "title": HTTPStatus(status).phrase}
    doc["status"] = status
    if detail:
        doc["detail"] = detail
    return {**doc, **{name: value for name, value in more.items() if name not in _MEMBERS}}, status


def problem_answer(e):
    """A Problem as its problem document, and its status: what it said as the detail, and its `more` beside it."""
    status, kind = next(answer for k, answer in KINDS.items() if isinstance(e, k))
    return problem(status, e.said, kind, **e.more)


BAD_REQUEST = problem_answer(Refused())

TOO_LARGE = (413, [(b"content-type", PROBLEM_JSON.encode())], json.dumps(problem(413, "payload too large")[0]).encode())


def post_body_cap(path):
    """The most a POST to `path` may carry, in bytes, checked against its Content-Length before
    the body is read: 38 MB where mail and its attachments go up, 1 MB everywhere else."""
    return MAX_SEND_POST if path in ("/api/send", "/api/draft") else MAX_POST


def _over_limit_answer(e):
    """The 402 an interactive AI request gets at the daily limit. The page asks the user, then
    sends the request again with AI_OVER_HEADER."""
    return problem(402, "today's AI spending has reached the daily limit", "over-limit", over_limit=True,
                   spent_micros=e.spent_micros, limit_micros=e.limit_micros)


def _ai_off_answer():
    """The 409 an AI request gets with AI off. Nothing was sent, and asking again won't help."""
    return problem(409, "AI is off: MailMind has no Claude API key", "ai-off", ai_off=True)


def failure_answer(e, method, path):
    """What a request that raised `e` answers, and its status. Anything it doesn't know is the fixed 500, with the class
    logged and nothing echoed: an exception's text can hold what a server or a message said."""
    if isinstance(e, _PROBLEMS):
        return problem_answer(e)
    if method == "POST":
        if isinstance(e, TypeError | ValueError):      # malformed request input (a bad or missing id or key) → clean 400
            return BAD_REQUEST
        if isinstance(e, profile.ReadOnlyError):       # a write this process may not make (and nothing queued it)
            log.info("POST %(path)s refused, read-only: %(what)s", {"path": path, "what": str(e)})
            return problem(409, "MailMind is running read-only", "read-only", read_only=True)
    if isinstance(e, OverLimit):                       # the page asks before it goes over
        return _over_limit_answer(e)
    if isinstance(e, AIOff):                           # no key: nothing was sent, and nothing will be until one is set
        return _ai_off_answer()
    log.error("%(method)s %(path)s failed", {"method": method, "path": path}, exc_info=e)
    return problem(500, "request failed")


def _no_one():
    pass


_someone_here = _no_one


def tell_of_someone(tell):
    """What `touched` tells when someone uses the app: mailmind.bootstrap.web binds the cost governor's."""
    global _someone_here
    _someone_here = tell


def touched(path):
    """Someone is here, for the cost governor's presence gate on drafting ahead. UI_QUIET routes
    are excluded because the SSE followup/outbox listeners re-fetch them even from a hidden,
    forgotten-open tab."""
    if path.startswith("/api/") and path not in UI_QUIET:
        _someone_here()


class ClientGone(Exception):
    """The client left before its request body arrived."""


async def read_body(scope, receive, cap):
    """A request's body off the ASGI server, up to `cap` bytes: b"" when it won't be read (a declared length over the cap, or
    that isn't one), or None when it runs past the cap without declaring a length."""
    declared = header_of(scope, b"content-length")
    if declared is not None:
        try:
            if not 0 <= int(declared) <= cap:
                return b""
        except ValueError:
            return b""                      # the perimeter answers a malformed length
    chunks, size = [], 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise ClientGone
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > cap:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def header_of(scope, name):
    for key, value in scope["headers"]:
        if key == name:
            return value
    return None


async def send_whole(send, status, headers, body):
    await send({"type": "http.response.start", "status": status,
                "headers": [*headers, (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body, "more_body": False})
