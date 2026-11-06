"""What a POST must carry: the page's CSRF token and this server's Origin."""
import secrets

from mailmind.platform.perimeter.errors import Refusal
from mailmind.platform.perimeter.fetch_metadata import refuse_cross_site
from mailmind.platform.perimeter.host import refuse_host
from mailmind.platform.perimeter.origin import own_origins


def token_matches(headers, token):
    """The request carries the page's CSRF token, compared in constant time."""
    return secrets.compare_digest(headers.get("X-CSRF-Token") or "", token)


def refuse_mutation(path, headers, *, port, token):
    """A POST must pass the host and cross-site checks, carry the CSRF token and carry this
    server's Origin: a browser sends one with every POST, the page's own fetches included."""
    refusal = refuse_host(headers, port) or refuse_cross_site(headers, path)
    if refusal:
        return refusal
    if not token_matches(headers, token):
        return Refusal(403, "bad or missing CSRF token")
    if (headers.get("Origin") or "").lower() not in own_origins(port):
        return Refusal(403, "origin required")
    return None
