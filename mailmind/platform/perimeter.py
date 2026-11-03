"""The perimeter: what a request must pass before any route runs.

Both front doors apply these checks, so the two refuse the same requests with the same answers.
Each check reads only the request's headers (any mapping whose .get ignores case: the old
server's message headers and Starlette's alike) and the values passed in, and returns a Refusal,
or None when the request may go on. Standard library only: python3 server.py runs it.

Every request reaches 127.0.0.1, so the danger is another page in the browser:
- a page on a rebound name, which reaches this server with its own Host (`refuse_host`);
- another site's no-cors GET, an <img>, which carries this server's Host and no Origin
  (`refuse_cross_site`);
- another site's POST, which can't carry the page's CSRF token (`refuse_mutation`).
"""
import secrets
from dataclasses import dataclass

# The routes that send mail or act on queued sends: these also require an Origin, and a matching
# one, where the other POSTs accept a request that carries none.
SEND_ROUTES = frozenset({"/api/send", "/api/draft", "/api/invite/rsvp", "/api/outbox/retry",
                         "/api/outbox/cancel", "/api/outbox/sendnow", "/api/cleanup/execute"})


@dataclass(frozen=True)
class Refusal:
    status: int
    error: str

    def answer(self):
        """The JSON body every refusal answers with."""
        return {"ok": False, "error": self.error}


def _own_hosts(port):
    return (f"127.0.0.1:{port}", f"localhost:{port}")


def _own_origins(port):
    return (f"http://127.0.0.1:{port}", f"http://localhost:{port}")


def token_matches(headers, token):
    """The request carries the page's CSRF token, compared in constant time."""
    return secrets.compare_digest(headers.get("X-CSRF-Token") or "", token)


def refuse_host(headers, port):
    """Block DNS rebinding: the Host must name this server, and an Origin, when sent, must be its
    own. A page on a rebound name reaches 127.0.0.1 with its own Host, so without this it could
    read the mailbox and the page's CSRF token."""
    host = (headers.get("Host") or "").lower()
    origin = headers.get("Origin")
    if host not in _own_hosts(port) or (origin and origin.lower() not in _own_origins(port)):
        return Refusal(403, "bad host/origin")
    return None


def refuse_cross_site(headers, path):
    """A GET another site started. It carries this server's Host and, as a no-cors request such
    as an <img>, no Origin, so the host check passes it, yet it could still make the server sync a
    folder, fetch bodies or run an AI briefing. Browsers mark it Sec-Fetch-Site cross-site, or
    same-site when it comes from another port on this machine. A top-level visit to the page
    itself stays allowed: it does nothing, and its contents can't be read across origins. A
    browser that sends no Sec-Fetch-Site is judged by the host check alone."""
    site = (headers.get("Sec-Fetch-Site") or "").lower()
    if site in ("", "same-origin", "none"):
        return None
    if path == "/" and headers.get("Sec-Fetch-Mode") == "navigate" and headers.get("Sec-Fetch-Dest") == "document":
        return None
    return Refusal(403, "cross-site request")


def refuse_body(headers, cap):
    """A body over `cap` bytes, or a length that isn't one, judged by Content-Length before the
    body is read."""
    try:
        length = int(headers.get("Content-Length", 0) or 0)
    except ValueError:
        length = -1
    if length < 0 or length > cap:
        return Refusal(413, "payload too large")
    return None


def refuse_mutation(path, headers, *, port, token):
    """A POST must pass the host check and carry the CSRF token; a send route must also carry a
    matching Origin, since a same-origin fetch always sends one."""
    refusal = refuse_host(headers, port)
    if refusal:
        return refusal
    if not token_matches(headers, token):
        return Refusal(403, "bad or missing CSRF token")
    if path in SEND_ROUTES and (headers.get("Origin") or "").lower() not in _own_origins(port):
        return Refusal(403, "origin required")
    return None


def refuse_get(path, headers, *, port):
    return refuse_host(headers, port) or refuse_cross_site(headers, path)


def refuse_post(path, headers, *, port, token, cap):
    return refuse_body(headers, cap) or refuse_mutation(path, headers, port=port, token=token)
