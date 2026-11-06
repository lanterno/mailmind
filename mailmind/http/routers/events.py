"""The live event stream."""
from mailmind.http.routing import ANSWERED, Routes
from mailmind.platform import sse, webroot


def _events(h):
    """The live event stream (mailmind.platform.sse): blocks this handler thread, passing on
    each event until the tab hangs up. A tab that reconnects resumes after the Last-Event-ID
    its EventSource sends, or gets one resync. A keepalive on a quiet stream finds a dead
    socket."""
    tab = sse.HUB.listen(h.headers.get("Last-Event-ID"))
    if tab is None:
        return h._json({"ok": False, "error": "too many listeners"}, 503)
    try:
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream")
        h.send_header("Cache-Control", "no-cache")
        h.send_header("Connection", "keep-alive")
        h.end_headers()
        h.wfile.write(tab.opening() + sse.hello(webroot.build()))
        h.wfile.flush()
        while True:
            events = tab.next(sse.HEARTBEAT)
            h.wfile.write(b"".join(e.frame() for e in events) or b": keepalive\n\n")
            h.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
        pass
    finally:
        tab.close()


def routes():
    r = Routes()

    @r.get("/api/events")
    def events(req):
        _events(req.handler)
        return ANSWERED

    return r
