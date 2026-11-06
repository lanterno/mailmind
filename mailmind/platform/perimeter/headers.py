"""The headers every answer carries."""
from mailmind.platform.perimeter.fetch_metadata import is_api

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


def answer_headers(path):
    """The headers every answer to `path` carries, as (name, value) pairs. A front door adds each
    one the route didn't set itself."""
    return _API_HEADERS if is_api(path) else _ANSWER_HEADERS
