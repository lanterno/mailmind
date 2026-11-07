"""The new front door: the app on FastAPI and uvicorn, `python -m mailmind serve [port]`.

It starts the same workers as `python3 server.py`, the old front door, through wiring.start, and
serves the same handler through a HandlerBridge, so either door can serve the same data
directory. The old one stays as the kill switch. Three differences: this door talks to the Claude
API through the Anthropic SDK, where the old one, with nothing installed, uses urllib, an
account whose config asks for IMAPClient ("imap_adapter": "imapclient") reaches its server
through it, where the old door keeps imaplib for every account, and it reads an invite through
icalendar and recurring-ical-events, where the old door keeps the hand-rolled reader.
"""
import dataclasses

from mailmind.bootstrap import assistant, calendar, wiring, mailbox, web
from mailmind.http import serving
from mailmind.http.app import create_app
from mailmind.http.bridge import HandlerBridge
from mailmind.platform import settings, webroot
from mailmind.platform.logging import get_logger


def build():
    """The ASGI app over the handler, and the coroutine function to await when
    shutdown begins. A POST's body may hold up to the route's own cap, and the bridge reads no
    other body: the handler reads none. The perimeter checks the page's token as the handler does, so
    both see the same one. AI calls go through the Anthropic SDK from here on, an account that
    asks for IMAPClient gets it, and invites are read through the library."""
    assistant.use("sdk")
    mailbox.offer("imapclient")
    calendar.use("icalendar")

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
    if not webroot.is_built():
        get_logger().warning("The app isn't built, so it answers 404. Run: just build-web")
    serving.Server(serving.config(app, port=port), before_shutdown).run()
