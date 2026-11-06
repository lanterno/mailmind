"""The new front door: the app on FastAPI and uvicorn, `python -m mailmind serve [port]`.

It starts the same workers as `python3 server.py`, the old front door, through wiring.start, and
serves the same handler through a HandlerBridge, so either door can serve the same data
directory. The old one stays as the kill switch. Two differences: this door talks to the Claude
API through the Anthropic SDK, where the old one, with nothing installed, uses urllib, and an
account whose config asks for IMAPClient ("imap_adapter": "imapclient") reaches its server
through it, where the old door keeps imaplib for every account.
"""
import dataclasses

from mailmind.bootstrap import assistant, wiring, mailbox, web
from mailmind.http import serving
from mailmind.http.app import create_app
from mailmind.http.bridge import HandlerBridge
from mailmind.platform import settings
from mailmind.platform.logging import get_logger


def build():
    """The ASGI app over the handler, and the coroutine function to await when
    shutdown begins. A POST's body may hold up to the route's own cap, and the bridge reads no
    other body: the handler reads none. The perimeter checks the page's token as the handler does, so
    both see the same one. AI calls go through the Anthropic SDK from here on, and an account
    that asks for IMAPClient gets it."""
    assistant.use("sdk")
    mailbox.offer("imapclient")

    def body_cap(method, path):
        return web.post_body_cap(path) if method == "POST" else 0
    bridge = HandlerBridge(web.H, body_cap=body_cap)
    return create_app(bridge, token=lambda: web.H.CSRF, body_cap=body_cap), bridge.close


def serve(port=None):
    if port is not None:
        settings.configure(dataclasses.replace(settings.current(), port=port))
    app, before_shutdown = build()      # first, so the startup lines name the SDK and who asked for IMAPClient
    wiring.start()
    port = settings.current().port
    get_logger().info("MailMind → http://127.0.0.1:%(port)s  (serving now)", {"port": port})
    serving.Server(serving.config(app, port=port), before_shutdown).run()
