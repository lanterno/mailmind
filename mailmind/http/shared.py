"""What a request to a route goes through (mailmind.http.route): what a failure answers, who counts as someone here, how far a
body may run. It imports nothing of FastAPI.
"""
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
            "/api/debug/health"}     # asking how the app is doing changes nothing, this included

# The status each answers with. 502 says asking again can help, and 422 that it won't.
STATUS = {NotFound: 404, Conflict: 409, Locked: 423, Refused: 400, Unreachable: 502, Unreadable: 422, Busy: 409}
_PROBLEMS = tuple(STATUS)

BAD_REQUEST = {"ok": False, "error": "bad request"}

TOO_LARGE = (413, [(b"content-type", b"text/plain; charset=utf-8")], b"payload too large")


def post_body_cap(path):
    """The most a POST to `path` may carry, in bytes, checked against its Content-Length before
    the body is read: 38 MB where mail and its attachments go up, 1 MB everywhere else."""
    return MAX_SEND_POST if path in ("/api/send", "/api/draft") else MAX_POST


def problem_answer(e):
    """A Problem as the app reads one, {ok: false, error}, and its status."""
    said = {"error": e.said} if e.said else {}
    return {"ok": False, **said, **e.more}, next(code for kind, code in STATUS.items() if isinstance(e, kind))


def _over_limit_answer(e):
    """The 402 an interactive AI request gets at the daily limit. The page asks the user, then
    sends the request again with AI_OVER_HEADER."""
    return {"ok": False, "error": "today's AI spending has reached the daily limit", "over_limit": True,
            "spent_micros": e.spent_micros, "limit_micros": e.limit_micros}, 402


def _ai_off_answer():
    """The 409 an AI request gets with AI off. Nothing was sent, and asking again won't help."""
    return {"ok": False, "error": "AI is off: MailMind has no Claude API key", "ai_off": True}, 409


def failure_answer(e, method, path):
    """What a request that raised `e` answers, and its status. Anything it doesn't know is the fixed 500, with the class
    logged and nothing echoed: an exception's text can hold what a server or a message said."""
    if isinstance(e, _PROBLEMS):
        return problem_answer(e)
    if method == "POST":
        if isinstance(e, TypeError | ValueError):      # malformed request input (bad/missing int id/pk) → clean 400
            return BAD_REQUEST, 400
        if isinstance(e, profile.ReadOnlyError):       # a write this process may not make (and nothing queued it)
            log.info("POST %(path)s refused, read-only: %(what)s", {"path": path, "what": str(e)})
            return {"ok": False, "error": "MailMind is running read-only", "read_only": True}, 409
    if isinstance(e, OverLimit):                       # the page asks before it goes over
        return _over_limit_answer(e)
    if isinstance(e, AIOff):                           # no key: nothing was sent, and nothing will be until one is set
        return _ai_off_answer()
    log.error("%(method)s %(path)s failed", {"method": method, "path": path}, exc_info=e)
    return {"ok": False, "error": "request failed"}, 500


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
