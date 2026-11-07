"""The ASGI app: FastAPI, with no routes of its own. Every request passes the perimeter, then
goes to the fallback, the handler behind a HandlerBridge, which holds the route table."""
from fastapi import FastAPI

from mailmind.http.middleware import Perimeter


def create_app(fallback, *, token, body_cap):
    """The app, answering with `fallback` (an ASGI app) wherever no route of its own matches,
    behind the perimeter (`token` and `body_cap` as Perimeter takes them). No docs or OpenAPI
    pages yet, and no trailing-slash redirects: a path matches exactly, as it does in
    the route table."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    app.router.default = fallback
    app.add_middleware(Perimeter, token=token, body_cap=body_cap)
    return app
