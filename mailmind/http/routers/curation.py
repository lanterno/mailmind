"""What the page asks of Curation: the rules, the Screener, a sender's cleanup, sweeps and the audit trail."""
from typing import Annotated

from fastapi import Query

from mailmind.http import named, route
from mailmind.http.schemas.common import answers
from mailmind.http.schemas.curation import (
    AuditArgs,
    AuditTrail,
    BatchCleanedUp,
    BatchCleanupBody,
    BatchPreview,
    BatchSendersBody,
    CleanedUp,
    CleanupBody,
    FilterChange,
    Filters,
    NoisySenders,
    RuleBody,
    RuleBook,
    RuleChanged,
    ScreenerArgs,
    ScreenerQueue,
    SenderArgs,
    SenderPreview,
    SweepBody,
    Swept,
)
from mailmind.kernel.errors import Problem
from mailmind.platform.logging import get_logger

log = get_logger("cleanup")


def router(curation, mailbox):
    r = route.router()

    @r.get("/api/rules", responses=answers(RuleBook))
    def rules():
        return {"rules": curation.rules.load()}

    @r.post("/api/rules", responses=answers(RuleChanged))
    def rule(body: RuleBody):
        return curation.screener.rule(named.with_copy(mailbox, body.model_dump(exclude_unset=True), gone=None))

    @r.post("/api/filters", responses=answers(Filters))
    def filters(body: FilterChange):
        return curation.filters.post(body.model_dump(exclude_unset=True))

    @r.get("/api/audit", responses=answers(AuditTrail))
    def audit(args: Annotated[AuditArgs, Query()]):
        return curation.AUDIT.read(args.limit)

    @r.get("/api/screener", responses=answers(ScreenerQueue))
    def screener(args: Annotated[ScreenerArgs, Query()]):
        return curation.screener.queue(args.account)

    @r.get("/api/cleanup/senders", responses=answers(NoisySenders))
    def cleanup_senders():
        return curation.cleanup.senders()

    @r.get("/api/cleanup/sender", responses=answers(SenderPreview))
    def cleanup_sender(args: Annotated[SenderArgs, Query()]):
        return curation.cleanup.sender(args.as_query())

    @r.post("/api/cleanup/execute", responses=answers(CleanedUp))
    def cleanup_execute(body: CleanupBody):
        try:
            return curation.cleanup.execute(body.model_dump(exclude_unset=True))
        except Problem:
            raise
        except Exception as ex:
            log.error("error", exc_info=ex)
            return route.problem(500, "cleanup failed")

    @r.post("/api/cleanup/batch/preview", responses=answers(BatchPreview))     # reads only: a POST for the list it names
    def cleanup_batch_preview(body: BatchSendersBody):
        return curation.cleanup.batch_preview(body.model_dump(exclude_unset=True))

    @r.post("/api/cleanup/batch", responses=answers(BatchCleanedUp))
    def cleanup_batch(body: BatchCleanupBody):
        try:
            return curation.cleanup.batch_execute(body.model_dump(exclude_unset=True))
        except Problem:
            raise
        except Exception as ex:
            log.error("error", exc_info=ex)
            return route.problem(500, "cleanup failed")

    @r.post("/api/sweep", responses=answers(Swept))
    def sweep(body: SweepBody):
        return curation.sweeps.run(body.account, bool(body.dry_run))

    return r
