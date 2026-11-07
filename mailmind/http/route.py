"""A route on a router: FastAPI's `APIRoute`, answering as the handler answers.

`Route` does what `H.do_GET` and `H.do_POST` do around a route, so a handler on a router holds none of it: the request id for the
log, the leave to go over the AI limit, who counts as someone here, and what a failure answers (`shared.failure_answer`). A body is
read as the handler read it, an answer is written as the handler wrote it, and a value a schema refuses is the `bad request` 400.

    r = router()

    @r.post("/api/snooze", responses=answers(Snoozed))
    def snooze(body: SnoozeBody):
        return attention.snoozing.snooze(body.model_dump(exclude_unset=True))

A handler is a plain `def`, since a request can wait a long time on IMAP or the Claude API and one blocking the event loop would
stop every other. It runs on a thread of AnyIO's pool, in a copy of the request's context, so the context variables reach it. It
returns an object (a 200), `(object, code)`, or a `Response` for what isn't JSON.

A `Response` runs after the handler has returned, on the event loop, and past the reach of what a failure answers. So a handler does
whatever can fail first, and an answer that streams is a `StreamingResponse` with a `__call__` of its own around the stream, for
what must be given back however it ends: a tab hanging up leaves its generator waiting at a `yield`, whose `finally` runs only
when it is collected.
"""
import inspect
import json
from functools import wraps
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter
from fastapi.datastructures import Default, DefaultPlaceholder
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.routing import Match

from mailmind.assistant.api import allowing_over
from mailmind.http import shared
from mailmind.platform.db import txguard
from mailmind.platform.logging import for_request, get_logger

log = get_logger("http")

_AS_SENT = "query_string_as_sent"     # the scope's, as `handle` found it


def router():
    """A router whose routes are all `Route`s. `mailmind.bootstrap.web` hands each to the app."""
    return APIRouter(route_class=Route)


def json_answer(obj, code=200):
    """An answer in the bytes the handler's `_json` writes: Starlette's JSONResponse writes others, and refuses NaN."""
    return Response(json.dumps(obj).encode(), code, media_type="application/json")


def _rendered(answer):
    if isinstance(answer, Response):
        return answer
    return json_answer(*answer) if isinstance(answer, tuple) else json_answer(answer)


def _first_of_each(query_string):
    """The query string as the handler read it (`parse_qs`): a name's first value, and a blank one is no value at all.
    FastAPI would take the last, and hand a number the blank."""
    given = parse_qs(query_string.decode("latin-1"))
    return urlencode({name: values[0] for name, values in given.items()}).encode()


def query_as_sent(request):
    """The query string as the client wrote it, for what repeats it rather than reads it."""
    return request.scope[_AS_SENT].decode("latin-1")


def _replaying(body, receive):
    """`receive`, with `body` as the whole of the request's body and what comes after it as it comes."""
    sent = False

    async def replay():
        nonlocal sent
        if sent:
            return await receive()
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}
    return replay


class Route(APIRoute):
    """`work` is the handler as written, called at each request, so a test can stand in for it."""

    def __init__(self, path, endpoint, *, response_model=Default(None), **kw):
        if inspect.iscoroutinefunction(endpoint):
            raise TypeError(f"{path}: a handler is a plain def, which runs on a thread of its own")
        if not isinstance(response_model, DefaultPlaceholder):
            raise TypeError(f"{path}: an answer's model goes in `responses`, which never filters or refuses an answer")
        self.work = endpoint

        @wraps(endpoint)
        def on_its_thread(*args, **kwargs):
            try:
                shared.touched(path)
                return _rendered(self.work(*args, **kwargs))
            finally:
                left = txguard.release_thread()
                if left:
                    log.warning("%(path)s left %(count)s transaction(s) open", {"path": path, "count": left})
        # response_model stays None however the handler is annotated, or FastAPI would filter its answers.
        super().__init__(path, on_its_thread, response_model=None, **kw)
        if "POST" in (self.methods or ()) and self.body_field is None:
            raise TypeError(f"{path}: a POST names what it reads, `NoBody` if nothing, or a body that isn't a JSON object goes unrefused")

    def matches(self, scope):
        """The method is part of what matches. FastAPI would answer a path it knows by another method with its own 405."""
        match, child = super().matches(scope)
        return (Match.NONE, {}) if match == Match.PARTIAL else (match, child)

    async def handle(self, scope, receive, send):
        """What FastAPI is to read: the query string, and a POST's body, as the handler read them. A body counts only if its
        length was declared (no length, as a chunked one has, is {}), whatever its Content-Type says, which FastAPI would
        hold to JSON. The perimeter has judged a declared length, so a body that runs over its cap never said how long it was."""
        scope[_AS_SENT] = scope.get("query_string", b"")
        scope["query_string"] = _first_of_each(scope[_AS_SENT])
        if scope["method"] == "POST":
            try:
                body = await shared.read_body(scope, receive, shared.post_body_cap(scope["path"]))
            except shared.ClientGone:
                return
            if body is None:
                return await shared.send_whole(send, *shared.TOO_LARGE)
            if shared.header_of(scope, b"content-length") is None or not body:
                body = b"{}"
            scope["headers"] = [*(h for h in scope["headers"] if h[0] != b"content-type"), (b"content-type", b"application/json")]
            receive = _replaying(body, receive)
        await super().handle(scope, receive, send)

    def get_route_handler(self):
        run = super().get_route_handler()

        async def answering(request):
            with for_request(), allowing_over(request.headers.get(shared.AI_OVER_HEADER) == "allow"):
                try:
                    return await run(request)
                except (RequestValidationError, HTTPException):     # a value a schema refuses, or a body that won't parse
                    return json_answer(shared.BAD_REQUEST, 400)
                except Exception as e:
                    return json_answer(*shared.failure_answer(e, request.method, request.scope["path"]))
        return answering
