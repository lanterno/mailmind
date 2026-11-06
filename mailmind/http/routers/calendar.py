"""What the page asks of Calendar: Today & next, and an answer to an invite."""
from mailmind.http.routing import Routes
from mailmind.platform.logging import get_logger

log = get_logger("rsvp")


def routes(calendar):
    r = Routes()

    @r.get("/api/upcoming")     # read-only: invites in fetched mail, nothing fetched
    def upcoming(req):
        return calendar.agenda.upcoming(req.arg("account") or None)

    @r.post("/api/invite/rsvp")
    def rsvp(req):
        try:
            return calendar.rsvp.respond(req.body)
        except Exception as ex:            # never leak a raw exception on a send route
            log.error("error", exc_info=ex)
            return {"ok": False, "error": "RSVP failed"}, 500

    return r
