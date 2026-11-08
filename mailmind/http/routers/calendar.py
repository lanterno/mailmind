"""What the page asks of Calendar: Today & next, an answer to an invite, and a file for an event someone confirmed."""
from mailmind.http import named, route
from mailmind.http.schemas.calendar import EventBody, EventFile, RsvpBody, RsvpQueued, Upcoming
from mailmind.http.schemas.common import answers
from mailmind.kernel.errors import Problem
from mailmind.platform.logging import get_logger

log = get_logger("rsvp")


def router(calendar, mailbox):
    r = route.router()

    @r.get("/api/upcoming", responses=answers(Upcoming))     # read-only: invites in fetched mail, nothing fetched
    def upcoming():
        return calendar.agenda.upcoming()

    @r.post("/api/invite/rsvp", responses=answers(RsvpQueued))
    def rsvp(body: RsvpBody):
        try:
            return calendar.rsvp.respond(named.with_message(mailbox, body.model_dump(exclude_unset=True)))
        except Problem:
            raise
        except Exception as ex:            # never leak a raw exception on a send route
            log.error("error", exc_info=ex)
            return route.problem(500, "RSVP failed")

    @r.post("/api/calendar/event", responses=answers(EventFile))     # a file for the person's calendar: nothing is sent or made
    def event_file(body: EventBody):
        return calendar.publishing.event(named.with_message(mailbox, body.model_dump(exclude_unset=True)))

    return r
