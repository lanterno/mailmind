"""The cap on a request's body."""
from mailmind.platform.perimeter.errors import Refusal


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
