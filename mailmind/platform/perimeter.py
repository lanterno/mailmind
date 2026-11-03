"""The perimeter: what a request must pass before any route runs.

Both front doors apply these checks, so the two refuse the same requests with the same answers.
Each check reads only the request's headers (any mapping whose .get ignores case: the old
server's message headers and Starlette's alike) and the values passed in, and returns a Refusal,
or None when the request may go on. Standard library only: python3 server.py runs it.

Every request reaches 127.0.0.1, so the danger is another page in the browser:
- a page on a rebound name, which reaches this server with its own Host (`refuse_host`);
- another site's no-cors GET, an <img>, which carries this server's Host and no Origin
  (`refuse_cross_site`);
- another site's POST, which can't carry the page's CSRF token or its Origin (`refuse_mutation`).
The API answers only the page's own requests, which browsers mark Sec-Fetch-Site: same-origin.
Every answer, a refusal included, carries the headers `answer_headers` names.
"""
import secrets
from dataclasses import dataclass

# What every answer carries, unless its route set the header itself.
_ANSWER_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),      # a body is only ever what its Content-Type says
    ("Referrer-Policy", "no-referrer"),         # a link out of the app names no page of it
    ("X-Frame-Options", "DENY"),                # no other page may frame the app and steer clicks
    ("Cross-Origin-Opener-Policy", "same-origin"),     # a page of another origin that opens the app,
                                                       # or that it opens, gets no handle on it
    ("Cross-Origin-Resource-Policy", "same-origin"),   # no other origin may load an answer as an
                                                       # image, a script or the like
    # The app asks for no device, sensor or payment; nothing it shows may either.
    ("Permissions-Policy", "accelerometer=(), camera=(), display-capture=(), geolocation=(), "
                           "gyroscope=(), hid=(), magnetometer=(), microphone=(), midi=(), "
                           "payment=(), serial=(), usb=()"),
)
# The API's answers hold the mailbox, so no cache keeps them.
_API_HEADERS = (*_ANSWER_HEADERS, ("Cache-Control", "no-store"))

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


def _api(path):
    return path == "/api" or path.startswith("/api/")


def refuse_cross_site(headers, path):
    """A request the page didn't make. Another site's no-cors GET, such as an <img>, carries this
    server's Host and no Origin, so the host check passes it, yet it could still make the server
    sync a folder, fetch bodies or run an AI briefing. Browsers mark it Sec-Fetch-Site cross-site,
    or same-site when it comes from another port on this machine.

    Under /api/ only same-origin passes: the page's own fetches, its event stream and its
    downloads. A request that says nothing (a browser too old to send the header, or not a
    browser) or comes from no page (a typed URL) is refused as well. The page itself, `/`, also
    opens from a typed or bookmarked URL, or from another site's link: a top-level visit does
    nothing, and its contents can't be read across origins."""
    site = (headers.get("Sec-Fetch-Site") or "").lower()
    if _api(path):
        if site == "same-origin":
            return None
        return Refusal(403, "cross-site request" if site in ("cross-site", "same-site")
                       else "same-origin request required")
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
    """A POST must pass the host and cross-site checks, carry the CSRF token and carry this
    server's Origin: a browser sends one with every POST, the page's own fetches included."""
    refusal = refuse_host(headers, port) or refuse_cross_site(headers, path)
    if refusal:
        return refusal
    if not token_matches(headers, token):
        return Refusal(403, "bad or missing CSRF token")
    if (headers.get("Origin") or "").lower() not in _own_origins(port):
        return Refusal(403, "origin required")
    return None


def answer_headers(path):
    """The headers every answer to `path` carries, as (name, value) pairs. A front door adds each
    one the route didn't set itself."""
    return _API_HEADERS if _api(path) else _ANSWER_HEADERS


def refuse_get(path, headers, *, port):
    return refuse_host(headers, port) or refuse_cross_site(headers, path)


def refuse_post(path, headers, *, port, token, cap):
    return refuse_body(headers, cap) or refuse_mutation(path, headers, port=port, token=token)
