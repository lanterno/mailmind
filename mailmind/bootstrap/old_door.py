"""The old front door: `python3 server.py [port]`, the app on the standard library's HTTP server.

It serves the same handler as the new door with nothing installed, which is why it stays while the new door carries daily
use: it is the kill switch.
"""
import dataclasses
import sys
from http.server import ThreadingHTTPServer

from mailmind.bootstrap import wiring, web
from mailmind.platform import settings


class _Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # A browser dropping a socket (reload, closed tab, unused preconnect) isn't a fault worth
        # a traceback; anything else still prints one.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


def main(argv):
    if len(argv) > 1:
        settings.configure(dataclasses.replace(settings.current(), port=int(argv[1])))
    wiring.start()  # the schema, the workers' wiring, and the workers
    port = settings.current().port
    print(f"MailMind → http://127.0.0.1:{port}  (serving now)", flush=True)
    _Server(("127.0.0.1", port), web.H).serve_forever()
