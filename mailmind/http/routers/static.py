"""The app's page, with this run's token in it, and its files. These open from a typed URL or another site's link, so they are no
part of the API, and opening one doesn't count as someone here."""
from fastapi import Request
from starlette.responses import Response

from mailmind.http import route
from mailmind.http.schemas.common import NotJson, answers
from mailmind.platform import perimeter, webroot

_NOT_JSON = answers(NotJson)


def _is_place(path):
    """A place in the app, as `app/route.ts` writes one."""
    return path in ("/all", "/search", "/documents") or path.startswith(("/m/", "/brief/", "/all/"))


def router():
    r = route.router()

    @r.get("/", responses=_NOT_JSON)
    @r.get("/all", responses=_NOT_JSON)
    @r.get("/all/{place:path}", responses=_NOT_JSON)
    @r.get("/search", responses=_NOT_JSON)
    @r.get("/documents", responses=_NOT_JSON)
    @r.get("/m/{place:path}", responses=_NOT_JSON)
    @r.get("/brief/{place:path}", responses=_NOT_JSON)
    def page():
        """The app, or how to make one: only a build holds it."""
        html = webroot.page(perimeter.CSRF)
        if html is None:
            return Response(b"The app isn't built. Run: just build-web\n", 404, headers={"Content-Type": "text/plain; charset=utf-8"})
        return Response(html.encode("utf-8"), headers={"Content-Type": "text/html; charset=utf-8",
                                                       "Content-Security-Policy": perimeter.page_policy(),
                                                       "Cache-Control": "no-store"})     # it holds this run's token

    @r.get("/next", responses=_NOT_JSON)
    @r.get("/next/{rest:path}", responses=_NOT_JSON)
    def moved(request: Request):
        """A link from when the app lived at /next. Only one of its own places goes on the redirect, so no path can send a
        browser anywhere else (`/next//elsewhere.example` would, as a path starting `//` names a host)."""
        rest = request.scope["path"].removeprefix("/next")
        query = route.query_as_sent(request)
        where = (rest if _is_place(rest) else "/") + (f"?{query}" if query else "")
        return Response(b"", 302, headers={"Content-Type": "text/plain", "Location": where})

    @r.get("/assets/{file:path}", responses=_NOT_JSON)
    def asset(request: Request):
        """One of the page's files, by ETag, compressed for a browser that takes it. `webroot.asset` reads the path as sent."""
        found = webroot.asset(request.scope["path"])
        if found is None:
            return Response(b"not found", 404, headers={"Content-Type": "text/plain"})
        headers = {"ETag": found.etag, "Cache-Control": webroot.FOREVER, "Vary": "Accept-Encoding"}
        if request.headers.get("If-None-Match") == found.etag:
            return Response(status_code=304, headers=headers)
        if found.gzipped and "gzip" in (request.headers.get("Accept-Encoding") or ""):
            return Response(found.gzipped, headers={"Content-Type": found.ctype, "Content-Encoding": "gzip", **headers})
        return Response(found.body, headers={"Content-Type": found.ctype, **headers})

    return r
