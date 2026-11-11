"""The app on FastAPI and uvicorn, `python -m mailmind serve [port]`.

It starts the workers through wiring.start and serves the routers. An account whose config asks for
IMAPClient ("imap_adapter": "imapclient") reaches its server through it. The command line, which doesn't
come through here, stays on imaplib.
"""
import dataclasses

from mailmind.bootstrap import wiring, mailbox, web
from mailmind.http import serving
from mailmind.http.app import create_app
from mailmind.platform import perimeter, settings, webroot
from mailmind.platform.logging import get_logger


def build():
    """The ASGI app over the routers. A POST's body may hold up to the route's own cap, and no other request's may hold anything.
    An account that asks for IMAPClient gets it from here on."""
    mailbox.offer("imapclient")

    def body_cap(method, path):
        return web.post_body_cap(path) if method == "POST" else 0
    return create_app(routers=web.ROUTERS, token=lambda: perimeter.CSRF, body_cap=body_cap)


def serve(port=None):
    if port is not None:
        settings.configure(dataclasses.replace(settings.current(), port=port))
    app = build()                       # first, so the startup lines name who asked for IMAPClient
    wiring.start()
    port = settings.current().port
    get_logger().info("MailMind → http://127.0.0.1:%(port)s  (serving now)", {"port": port})
    if not webroot.is_built():
        get_logger().warning("The app isn't built, so it answers 404. Run: just build-web")
    serving.Server(serving.config(app, port=port)).run()
