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
    shutdown begins. The bridge reads a POST's body up to the route's own cap, and no other body:
    server.H reads none."""
    def body_cap(method, path):
        return server.post_body_cap(path) if method == "POST" else 0
    bridge = HandlerBridge(server.H, body_cap=body_cap)
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
