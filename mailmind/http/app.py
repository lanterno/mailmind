"""The ASGI app: FastAPI. Every request passes the perimeter, then goes to a route on one of its routers, or else to the
fallback, the handler behind a HandlerBridge, which holds the routes not yet moved."""
from contextlib import asynccontextmanager

from anyio import to_thread
from fastapi import FastAPI

from mailmind.http.middleware import Perimeter

# Requests a router's handlers may have running at once. Each holds a thread, for as long as an IMAP server or the Claude API
# takes to answer, so the 40 AnyIO starts with could all be waiting.
THREADS = 64


@asynccontextmanager
async def _enough_threads(_app):
    """AnyIO's limit is the running event loop's, so it can only be raised from inside it."""
    to_thread.current_default_thread_limiter().total_tokens = THREADS
    yield


def create_app(fallback, *, token, body_cap, routers=()):
    """The app, answering with `routers` and with `fallback` (an ASGI app) wherever none of them matches, behind the perimeter
    (`token` and `body_cap` as Perimeter takes them). No docs or OpenAPI pages, so nothing new is exposed: a script reaches
    the schema through FastAPI's get_openapi. No trailing-slash redirects: a path matches exactly."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False, lifespan=_enough_threads)
    app.router.default = fallback
    for router in routers:
        app.include_router(router)
    app.add_middleware(Perimeter, token=token, body_cap=body_cap)
    return app
