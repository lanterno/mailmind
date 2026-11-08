"""What the page asks of Calendar: Today & next, and an answer to an invite."""
from mailmind.http import route
from mailmind.http.schemas.calendar import RsvpBody, RsvpQueued, Upcoming
from mailmind.http.schemas.common import answers
from mailmind.kernel.errors import Problem
from mailmind.platform.logging import get_logger

log = get_logger("rsvp")


def router(calendar):
    r = route.router()

    @r.get("/api/upcoming", responses=answers(Upcoming))     # read-only: invites in fetched mail, nothing fetched
    def upcoming():
        return calendar.agenda.upcoming()

    @r.post("/api/invite/rsvp", responses=answers(RsvpQueued))
    def rsvp(body: RsvpBody):
        try:
            return calendar.rsvp.respond(body.model_dump(exclude_unset=True))
        except Problem:
            raise
        except Exception as ex:            # never leak a raw exception on a send route
            log.error("error", exc_info=ex)
            return route.problem(500, "RSVP failed")

    return r
