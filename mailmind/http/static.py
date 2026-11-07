"""The app's page, with this run's token in it, and its files."""
from urllib.parse import urlsplit

from mailmind.http.routing import ANSWERED, Routes
from mailmind.platform import perimeter, webroot


def _page(h):
    """The app, or how to make one: only a build holds it."""
    html = webroot.page(h.CSRF)
    if html is None:
        return h._send(404, b"The app isn't built. Run: just build-web\n", "text/plain; charset=utf-8")
    h._send(200, html.encode("utf-8"), "text/html; charset=utf-8",
            [("Content-Security-Policy", perimeter.page_policy()),
             ("Cache-Control", "no-store")])     # it holds this run's token


def _asset(h, path):
    """One of the page's files, by ETag, compressed for a browser that takes it."""
    found = webroot.asset(path)
    if found is None:
        return h._send(404, b"not found", "text/plain")
    headers = [("ETag", found.etag), ("Cache-Control", webroot.FOREVER), ("Vary", "Accept-Encoding")]
    if h.headers.get("If-None-Match") == found.etag:
        h.send_response(304)
        for k, v in headers:
            h.send_header(k, v)
        return h.end_headers()
    body = found.body
    if found.gzipped and "gzip" in (h.headers.get("Accept-Encoding") or ""):
        body = found.gzipped
        headers.append(("Content-Encoding", "gzip"))
    h._send(200, body, found.ctype, headers)


def _is_place(path):
    """A place in the app, as `app/route.ts` writes one."""
    return path in ("/all", "/search") or path.startswith(("/m/", "/brief/"))


def routes():
    r = Routes()

    @r.get("/")
    def home(req):
        _page(req.handler)
        return ANSWERED

    @r.matching("GET", _is_place)
    def place(req):
        _page(req.handler)
        return ANSWERED

    @r.matching("GET", lambda path: path == "/next" or path.startswith("/next/"))
    def moved(req):
        """A link from when the app lived at /next. Only one of its own places goes on the redirect, so no path can send a
        browser anywhere else (`/next//elsewhere.example` would, as a path starting `//` names a host)."""
        rest = req.path.removeprefix("/next")
        query = urlsplit(req.handler.path).query
        where = (rest if _is_place(rest) else "/") + (f"?{query}" if query else "")
        req.handler._send(302, b"", "text/plain", [("Location", where)])
        return ANSWERED

    @r.matching("GET", lambda path: path.startswith(webroot.PREFIX))
    def asset(req):
        _asset(req.handler, req.path)
        return ANSWERED

    return r
