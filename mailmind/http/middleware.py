"""The perimeter as ASGI middleware: the checks in mailmind.platform.perimeter, run on every
request before any route, so no route can forget one, and before a body is read."""
import json

from starlette.datastructures import Headers

from mailmind.platform import perimeter, settings

# What the old server's handler sends with every answer (server.H._send), so a refusal reads the
# same from either front door.
_ANSWER_HEADERS = [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"),
                   (b"x-frame-options", b"DENY")]


class Perimeter:
    """`token()` is the page's CSRF token; `body_cap(method, path)` the most a request's body may
    hold, with the path as sent. A GET (or HEAD) must pass the host and cross-site checks; any
    other method, the size cap, the host and cross-site checks, the token and the Origin."""

    def __init__(self, app, *, token, body_cap):
        self.app = app
        self.token = token
        self.body_cap = body_cap

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        method = scope["method"]
        path = (scope.get("raw_path") or scope["path"].encode()).decode("latin-1")
        port = settings.current().port
        if method in ("GET", "HEAD"):
            refusal = perimeter.refuse_get(path, headers, port=port)
        else:
            refusal = perimeter.refuse_post(path, headers, port=port, token=self.token(),
                                            cap=self.body_cap(method, path))
        if refusal is None:
            return await self.app(scope, receive, send)
        body = json.dumps(refusal.answer()).encode()
        await send({"type": "http.response.start", "status": refusal.status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()), *_ANSWER_HEADERS]})
        await send({"type": "http.response.body", "body": body})
