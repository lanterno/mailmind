"""The new front door: the app on FastAPI and uvicorn, `python -m mailmind serve [port]`.

It starts the same workers as `python3 server.py`, the old front door, through legacy.start, and
serves the same handler through a HandlerBridge, so either door can serve the same data
directory. The old one stays as the kill switch. Two differences: this door talks to the Claude
API through the Anthropic SDK, where the old one, with nothing installed, uses urllib, and an
account whose config asks for IMAPClient ("imap_adapter": "imapclient") reaches its server
through it, where the old door keeps imaplib for every account.
"""
import dataclasses

from mailmind.bootstrap import assistant, legacy, mailbox
from mailmind.http import serving
from mailmind.http.app import create_app
from mailmind.http.bridge import HandlerBridge
from mailmind.platform import settings


def build(server):
    """The ASGI app over the legacy server module, and the coroutine function to await when
    shutdown begins. A POST's body may hold up to the route's own cap, and the bridge reads no
    other body: server.H reads none. The perimeter checks the page's token as server.H does, so
    both see the same one. AI calls go through the Anthropic SDK from here on, and an account
    that asks for IMAPClient gets it."""
    assistant.use("sdk")
    mailbox.offer("imapclient")

    def body_cap(method, path):
        return server.post_body_cap(path) if method == "POST" else 0
    bridge = HandlerBridge(server.H, body_cap=body_cap)
    return create_app(bridge, token=lambda: server.CSRF, body_cap=body_cap), bridge.close


def serve(port=None):
    if port is not None:
        settings.configure(dataclasses.replace(settings.current(), port=port))
    server = legacy.server_module()
    app, before_shutdown = build(server)      # first, so the startup lines name the SDK and who asked for IMAPClient
    legacy.start()
    port = settings.current().port
    print(f"MailMind → http://127.0.0.1:{port}  (serving now)", flush=True)
    serving.Server(serving.config(app, port=port), before_shutdown).run()
