"""The React app, the old page at /legacy, and their files, each page with this run's token in it."""
from urllib.parse import urlsplit

from mailmind.http.routing import ANSWERED, Routes
from mailmind.platform import perimeter, webroot


def _page(h, html, policy):
    h._send(200, html.encode("utf-8"), "text/html; charset=utf-8",
            [("Content-Security-Policy", policy),
             ("Cache-Control", "no-store")])     # it holds this run's token


def _asset(h, path):
    """One of the page's files, by ETag, compressed for a browser that takes it."""
    found = webroot.asset(path)
    if found is None:
        return h._send(404, b"not found", "text/plain")
    headers = [("ETag", found.etag), ("Cache-Control", found.cache), ("Vary", "Accept-Encoding")]
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
    """A place in the React app, as `app/route.ts` writes one."""
    return path in ("/all", "/search") or path.startswith(("/m/", "/brief/"))


def _old_page(h):
    _page(h, webroot.page(h.CSRF), perimeter.page_policy())


def routes():
    r = Routes()

    @r.get("/")
    def home(req):
        """The React app. With nothing built the old page stands in, so `python3 server.py` still needs no build."""
        built = webroot.next_page(req.handler.CSRF)
        if built is None:
            _old_page(req.handler)
        else:
            _page(req.handler, built, perimeter.next_page_policy())
        return ANSWERED

    @r.get("/legacy")
    def legacy(req):
        _old_page(req.handler)
        return ANSWERED

    @r.matching("GET", _is_place)
    def place(req):
        built = webroot.next_page(req.handler.CSRF)
        if built is None:
            req.handler._send(404, b"The app isn't built. Run: just build-web\n", "text/plain; charset=utf-8")
        else:
            _page(req.handler, built, perimeter.next_page_policy())
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
