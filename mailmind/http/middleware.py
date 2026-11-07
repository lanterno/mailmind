"""The perimeter as ASGI middleware: the checks in mailmind.platform.perimeter, run on every
request before any route, so no route can forget one, and before a body is read; and the headers
every answer carries, added to any answer that didn't set them."""
import json

from starlette.datastructures import Headers

from mailmind.platform import perimeter, settings


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
        send = _adding(send, perimeter.answer_headers(path))
        port = settings.current().port
        if method in ("GET", "HEAD"):
            refusal = perimeter.refuse_get(path, headers, port=port)
        else:
            refusal = perimeter.refuse_post(path, headers, port=port, token=self.token(),
                                            cap=self.body_cap(method, path))
        if refusal is None:
            # The ASGI server decodes `path`, so /%61pi/digest would pass here as no part of the API and match a route as
            # /api/digest. Whatever runs next goes by the path as judged, and as sent.
            return await self.app({**scope, "path": path}, receive, send)
        body = json.dumps(refusal.answer()).encode()
        await send({"type": "http.response.start", "status": refusal.status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def _adding(send, headers):
    """`send`, with each of `headers` added to the answer's start unless the answer set it."""
    extra = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers]

    async def send_adding(message):
        if message["type"] == "http.response.start":
            have = {k.lower() for k, _ in message.get("headers", ())}
            message = {**message, "headers": [*message.get("headers", ()),
                                              *(h for h in extra if h[0] not in have)]}
        await send(message)
    return send_adding
