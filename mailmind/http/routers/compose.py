"""What the page asks of Compose: a send, a draft and the outbox."""
from typing import Annotated

from fastapi import Query

from mailmind.http import named, route
from mailmind.http.schemas.common import answers
from mailmind.http.schemas.compose import (
    Cancelled,
    DraftBody,
    DraftChanged,
    DraftDiscarded,
    DraftOpened,
    DraftSaved,
    NamesASend,
    NothingToSave,
    Outbox,
    OutboxArgs,
    Preview,
    PreviewBody,
    Queued,
    ResumeBody,
    Retried,
    SendBody,
    SentNow,
)
from mailmind.kernel.errors import Conflict, NotFound, Problem, Refused
from mailmind.platform.logging import get_logger

log = get_logger("send")
resume_log = get_logger("resume")


def router(compose, mailbox):
    r = route.router()

    def composed(body):
        """A send's or a save's fields, with `in_reply_to_pk` for a copy of the original its `original_key` names. One that
        has gone stops a reply or forward from being written, but not a reopened draft's save or send, which carries its own
        quote and threading."""
        asked = body.model_dump(exclude_unset=True)
        asked["in_reply_to_pk"] = None
        if body.original_key is not None:
            pk = mailbox.queries.pk_of_message(body.original_key)
            if pk is None and body.resume is None and body.mode in ("reply", "replyall", "forward"):
                raise NotFound("original message not found")
            asked["in_reply_to_pk"] = pk
        return asked

    @r.get("/api/outbox", responses=answers(Outbox))
    def outbox(args: Annotated[OutboxArgs, Query()]):
        return {**compose.outbox.list_recent(args.limit), "drafts": compose.drafts.waiting()}

    @r.post("/api/send", responses=answers(Queued))
    def send(body: SendBody):
        try:
            return compose.sending.send(composed(body))
        except Problem:
            raise
        except ValueError as ve:           # our own validation messages are secret-free
            raise Refused(str(ve)) from ve
        except Exception as ex:            # never leak a raw exception on the send route
            log.error("error", exc_info=ex)
            return route.problem(500, "send failed")

    @r.post("/api/outbox/retry", responses=answers(Retried))
    def outbox_retry(body: NamesASend):
        answer = compose.outbox.retry(body.id)
        if not answer["ok"]:
            raise NotFound()
        return answer

    @r.post("/api/outbox/cancel", responses=answers(Cancelled))
    def outbox_cancel(body: NamesASend):
        if not compose.outbox.cancel(body.id):
            raise Conflict("too late to cancel")
        return {"ok": True}

    @r.post("/api/outbox/sendnow", responses=answers(SentNow))
    def outbox_send_now(body: NamesASend):
        if not compose.outbox.send_now(body.id):
            raise NotFound()
        return {"ok": True}

    @r.post("/api/draft", responses=answers(DraftSaved | NothingToSave | DraftDiscarded, {409: DraftChanged}))
    def draft(body: DraftBody):
        try:
            return compose.drafts.save(composed(body))
        except ValueError as ve:
            raise Refused(str(ve)) from ve

    @r.post("/api/compose/preview", responses=answers(Preview))       # read-only: what you typed, as it would go out
    def compose_preview(body: PreviewBody):
        return compose.composing.preview(body.text or "")

    @r.post("/api/draft/resume", responses=answers(DraftOpened))      # read-only: a saved draft, back into the composer
    def draft_resume(body: ResumeBody):
        try:
            return compose.drafts.resume(named.with_copy(mailbox, body.model_dump(exclude_unset=True),
                                                         gone="That draft is no longer in Drafts."))
        except Problem:
            raise
        except Exception as ex:
            resume_log.error("error", exc_info=ex)
            return route.problem(500, "Couldn’t open this draft.")

    return r
