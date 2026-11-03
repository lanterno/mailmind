"""The new front door: the app on FastAPI and uvicorn, `python -m mailmind serve [port]`.

It starts the same workers as `python3 server.py`, the old front door, through legacy.start, and
serves the same handler through a HandlerBridge, so either door can serve the same data
directory. The old one stays as the kill switch.
"""
import dataclasses

from mailmind.bootstrap import legacy
from mailmind.http import serving
from mailmind.http.app import create_app
from mailmind.http.bridge import HandlerBridge
from mailmind.platform import settings


def build(server):
    """The ASGI app over the legacy server module, and the coroutine function to await when
    shutdown begins. The bridge reads a body up to the largest cap any route allows; the handler
    applies each route's own."""
    bridge = HandlerBridge(server.H, max_body=server.MAX_SEND_POST)
    return create_app(bridge), bridge.close


def serve(port=None):
    if port is not None:
        settings.configure(dataclasses.replace(settings.current(), port=port))
    server = legacy.server_module()
    legacy.start(server)
    app, before_shutdown = build(server)
    port = settings.current().port
    print(f"MailMind → http://127.0.0.1:{port}  (serving now)", flush=True)
    serving.Server(serving.config(app, port=port), before_shutdown).run()
