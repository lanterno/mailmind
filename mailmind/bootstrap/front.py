"""The app on FastAPI and uvicorn, `python -m mailmind serve [port]`.

It starts the workers through wiring.start and serves the routers. It talks
to the Claude API through the Anthropic SDK, and an account whose config asks for IMAPClient
("imap_adapter": "imapclient") reaches its server through it. The command line, which doesn't come
through here, does neither.
"""
import dataclasses

from mailmind.bootstrap import assistant, wiring, mailbox, web
from mailmind.http import serving
from mailmind.http.app import create_app
from mailmind.platform import settings, webroot
from mailmind.platform.logging import get_logger


def build():
    """The ASGI app over the routers. A POST's body may hold up to the route's own cap, and no other request's may hold anything. AI
    calls go through the Anthropic SDK from here on, and an account that asks for IMAPClient gets it."""
    assistant.use("sdk")
    mailbox.offer("imapclient")

    def body_cap(method, path):
        return web.post_body_cap(path) if method == "POST" else 0
    return create_app(routers=web.ROUTERS, token=lambda: web.H.CSRF, body_cap=body_cap)


def serve(port=None):
    if port is not None:
        settings.configure(dataclasses.replace(settings.current(), port=port))
    app = build()                       # first, so the startup lines name the SDK and who asked for IMAPClient
    wiring.start()
    port = settings.current().port
    get_logger().info("MailMind → http://127.0.0.1:%(port)s  (serving now)", {"port": port})
    if not webroot.is_built():
        get_logger().warning("The app isn't built, so it answers 404. Run: just build-web")
    serving.Server(serving.config(app, port=port)).run()
