"""The ASGI app the new front door serves: FastAPI, with no routes of its own yet. Every request
goes to the fallback, the legacy handler behind a HandlerBridge, until routes move here."""
from fastapi import FastAPI


def create_app(fallback):
    """The app, answering with `fallback` (an ASGI app) wherever no route of its own matches.
    No docs or OpenAPI pages yet, and no trailing-slash redirects: a path matches exactly, as it
    does in server.py's chain of literal paths."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    app.router.default = fallback
    return app
