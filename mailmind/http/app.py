"""The ASGI app the new front door serves: FastAPI, with no routes of its own yet. Every request
passes the perimeter, then goes to the fallback, the legacy handler behind a HandlerBridge, until
routes move here."""
from fastapi import FastAPI

from mailmind.http.middleware import Perimeter


def create_app(fallback, *, token, body_cap):
    """The app, answering with `fallback` (an ASGI app) wherever no route of its own matches,
    behind the perimeter (`token` and `body_cap` as Perimeter takes them). No docs or OpenAPI
    pages yet, and no trailing-slash redirects: a path matches exactly, as it does in server.py's
    chain of literal paths."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    app.router.default = fallback
    app.add_middleware(Perimeter, token=token, body_cap=body_cap)
    return app
