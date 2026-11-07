"""The ASGI app: FastAPI. Every request passes the perimeter, then goes to a route on one of its routers, or else to `_no_route`."""
from contextlib import asynccontextmanager

from anyio import to_thread
from fastapi import FastAPI

from mailmind.http.middleware import Perimeter
from mailmind.http import shared

# Requests a router's handlers may have running at once. Each holds a thread, for as long as an IMAP server or the Claude API
# takes to answer, so the 40 AnyIO starts with could all be waiting.
THREADS = 64


@asynccontextmanager
async def _enough_threads(_app):
    """AnyIO's limit is the running event loop's, so it can only be raised from inside it."""
    to_thread.current_default_thread_limiter().total_tokens = THREADS
    yield


async def _no_route(scope, _receive, send):
    """What no route matches, on a path or an absolute-form request line alike: GET and POST are all the app answers, so any other
    method is one it doesn't implement. It reads no body, which the perimeter has judged already and nothing here wants. A GET or
    a POST of the API still says someone is here, as any that gets past the perimeter does."""
    plain = [(b"content-type", b"text/plain")]
    if scope["method"] in ("GET", "POST"):
        shared.touched(scope["path"])
        await shared.send_whole(send, 404, plain, b"not found")
    else:
        await shared.send_whole(send, 501, plain, b"not implemented")


def create_app(*, token, body_cap, routers=()):
    """The app, answering with `routers`, behind the perimeter (`token` and `body_cap` as Perimeter takes them). No docs or OpenAPI
    pages, so nothing new is exposed: a script reaches the schema through FastAPI's get_openapi. No trailing-slash redirects: a
    path matches exactly."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False, lifespan=_enough_threads)
    app.router.default = _no_route
    for router in routers:
        app.include_router(router)
    app.add_middleware(Perimeter, token=token, body_cap=body_cap)
    return app
