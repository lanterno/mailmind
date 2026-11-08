"""What the page asks of the Assistant. A route that spends is a POST, and the GETs read what is stored; the one GET that
can start a call is drafting ahead's kick, which needs the page's token."""
from functools import wraps
from typing import Annotated

from fastapi import Query, Request

from mailmind.assistant.api import NotSent
from mailmind.http import named, route
from mailmind.http.schemas.assistant import (
    Answered,
    AskBody,
    Brief,
    BriefMissing,
    ConfirmBody,
    Delta,
    DeltaArgs,
    DeltaBody,
    DeltaEmpty,
    DeltaMissing,
    DraftAheadArgs,
    DraftAheadReady,
    DraftAheadStatus,
    DraftedReply,
    DraftReplyBody,
    Expired,
    PlanBody,
    PlanCarriedOut,
    PlanIsASearch,
    PlanPreview,
    Review,
    ReviewMissing,
    Rewritten,
    RewriteBody,
    SummarizeBody,
    Summary,
    Unavailable,
)
from mailmind.http.schemas.common import Failure, NoBody, answers
from mailmind.kernel.errors import Problem
from mailmind.platform import perimeter
from mailmind.platform.logging import get_logger

log = get_logger("ai")

_MAY_FAIL = {502: Unavailable}


def _scrubbed(what, failed):
    """A handler whose failure is logged and answered in general terms. What `Route` answers goes through: a Problem, the
    402 (the page asks) and the 409."""
    def scrubbing(handler):
        @wraps(handler)
        def answering(*args, **kwargs):
            try:
                return handler(*args, **kwargs)
            except (NotSent, Problem):
                raise
            except Exception as ex:
                log.error("%(what)s", {"what": what}, exc_info=ex)
                return route.problem(500, failed)
        return answering
    return scrubbing


def router(assistant, mailbox):
    r = route.router()

    @r.get("/api/briefing", responses=answers(Brief | BriefMissing))     # what is stored of the "Today" card; POST writes it
    def briefing():
        return assistant.asking.briefing(generate=False)

    @r.get("/api/review", responses=answers(Review | ReviewMissing))     # what is stored of the Weekly Review; POST writes it
    def review():
        return assistant.asking.review(generate=False)

    @r.get("/api/briefing/since", responses=answers(Delta | DeltaEmpty | DeltaMissing))     # what is stored of "since you last looked"
    def delta(args: Annotated[DeltaArgs, Query()]):
        return assistant.asking.delta(args.as_query(), generate=False)

    @r.get("/api/ai/draft-ahead", responses=answers(DraftAheadStatus | DraftAheadReady))    # serve/peek; the KICK needs the CSRF token
    def draft_ahead(request: Request, args: Annotated[DraftAheadArgs, Query()]):
        asked = args.as_query()
        pk = mailbox.queries.pk_of_message(args.key, args.folder, args.uid)
        if pk is None:                      # nothing of it left to draft a reply to
            return {"ok": True, "status": "none"}
        asked["pk"] = [str(pk)]
        return assistant.drafting.get(asked, kick_ok=perimeter.token_matches(request.headers, perimeter.CSRF))

    @r.post("/api/ai/summarize", responses=answers(Summary, {**_MAY_FAIL, 504: Failure}))
    @_scrubbed("error", "summary failed")
    def summarize(body: SummarizeBody):
        summary = assistant.asking.summarize(named.with_message(mailbox, body.model_dump(exclude_unset=True)))
        return route.problem(504, "still summarizing") if summary is None else summary

    @r.post("/api/ai/draft-reply", responses=answers(DraftedReply, _MAY_FAIL))
    @_scrubbed("draft error", "draft failed")
    def draft_reply(body: DraftReplyBody):
        return assistant.drafting.reply(named.with_message(mailbox, body.model_dump(exclude_unset=True)))

    @r.post("/api/ai/rewrite", responses=answers(Rewritten, _MAY_FAIL))
    @_scrubbed("rewrite error", "rewrite failed")
    def rewrite(body: RewriteBody):
        return assistant.asking.rewrite(body.model_dump(exclude_unset=True))

    @r.post("/api/ai/ask", responses=answers(Answered, _MAY_FAIL))
    @_scrubbed("ask error", "ask failed")
    def ask(body: AskBody):
        return assistant.asking.ask(body.model_dump(exclude_unset=True))

    @r.post("/api/ai/do-confirm", responses=answers(PlanCarriedOut, {410: Expired}))    # carry out the plan a preview froze
    def do_confirm(body: ConfirmBody):
        plan = assistant.bulk.take(str(body.plan or ""))
        if plan is None:
            return route.problem(410, "this plan has expired", expired=True)
        return mailbox.filing.carry_out(plan.verb, plan.preset, plan.messages, body.undo_group)

    # read-only: NL command → validated, capped action PREVIEW
    @r.post("/api/ai/do-plan", responses=answers(PlanPreview | PlanIsASearch, _MAY_FAIL))
    @_scrubbed("do-plan error", "plan failed")
    def do_plan(body: PlanBody):
        return assistant.bulk.plan(body.model_dump(exclude_unset=True))

    # write each once, if none is stored
    @r.post("/api/briefing", responses=answers(Brief, _MAY_FAIL))
    @_scrubbed("briefing error", "briefing failed")
    def write_briefing(body: NoBody):
        return assistant.asking.briefing()

    @r.post("/api/review", responses=answers(Review, _MAY_FAIL))
    @_scrubbed("review error", "review failed")
    def write_review(body: NoBody):
        return assistant.asking.review()

    @r.post("/api/briefing/since", responses=answers(Delta | DeltaEmpty, _MAY_FAIL))
    @_scrubbed("delta error", "delta failed")
    def write_delta(body: DeltaBody):
        return assistant.asking.delta(body.model_dump(exclude_unset=True))

    # write each again
    @r.post("/api/review/refresh", responses=answers(Review, _MAY_FAIL))
    @_scrubbed("review error", "review failed")
    def refresh_review(body: NoBody):
        return assistant.asking.review(force=True)

    @r.post("/api/briefing/refresh", responses=answers(Brief, _MAY_FAIL))
    @_scrubbed("briefing error", "briefing failed")
    def refresh_briefing(body: NoBody):
        return assistant.asking.briefing(force=True)

    return r
