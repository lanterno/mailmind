"""The Host check, against DNS rebinding."""
from mailmind.platform.perimeter.errors import Refusal
from mailmind.platform.perimeter.origin import own_origins


def _own_hosts(port):
    return (f"127.0.0.1:{port}", f"localhost:{port}")


def refuse_host(headers, port):
    """Block DNS rebinding: the Host must name this server, and an Origin, when sent, must be its
    own. A page on a rebound name reaches 127.0.0.1 with its own Host, so without this it could
    read the mailbox and the page's CSRF token."""
    host = (headers.get("Host") or "").lower()
    origin = headers.get("Origin")
    if host not in _own_hosts(port) or (origin and origin.lower() not in own_origins(port)):
        return Refusal(403, "bad host/origin")
    return None
