"""The page, the React app beside it, and their files, each page with this run's token in it."""
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


def routes():
    r = Routes()

    @r.get("/")
    def page(req):
        _page(req.handler, webroot.page(req.handler.CSRF), perimeter.page_policy())
        return ANSWERED

    @r.matching("GET", lambda path: path == "/next" or path.startswith("/next/"))
    def next_app(req):
        built = webroot.next_page(req.handler.CSRF)
        if built is None:
            req.handler._send(404, b"The new app isn't built. Run: just build-web\n", "text/plain; charset=utf-8")
        else:
            _page(req.handler, built, perimeter.next_page_policy())
        return ANSWERED

    @r.matching("GET", lambda path: path.startswith(webroot.PREFIX))
    def asset(req):
        _asset(req.handler, req.path)
        return ANSWERED

    return r
