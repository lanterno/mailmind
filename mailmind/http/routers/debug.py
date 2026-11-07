"""What the app says of its own running: the connection pool, the background jobs, what AI cost, and all
of that at a glance."""
from typing import Annotated

from fastapi import Query

from mailmind.http import route
from mailmind.http.schemas.common import answers
from mailmind.http.schemas.debug import AiCosts, AtAGlance, DaysArgs, Jobs, PoolStats
from mailmind.kernel.when import now_iso
from mailmind.platform import profile


def router(mailbox, compose, background, assistant):
    r = route.router()

    @r.get("/api/debug/health", responses=answers(AtAGlance))  # read-only, and it never counts as someone here: ids and counts, never an address
    def health():
        idle, governor = mailbox.realtime.watching(), assistant.governor().snapshot()
        return {"ok": True, "profile": profile.current().value, "generated": now_iso(),
                "jobs": background.JOBS.snapshot(),
                "idle": {"accounts": len(idle), "running": sum(idle.values())},
                "queues": {"outbox": compose.outbox.QUEUE.counts(), "commands": mailbox.commands.QUEUE.counts(),
                           "drafts_waiting": len(compose.drafts.waiting())},
                "breakers": governor["breaker"],
                "ai": {"today": assistant.calls.usage(1)[0], "limit_micros": governor["limit_micros"],
                       "batch": assistant.assessments.batch_status()}}

    @r.get("/api/debug/pool", responses=answers(PoolStats))
    def pool():
        return {"ok": True, **mailbox.pool.stats()}

    @r.get("/api/debug/jobs", responses=answers(Jobs))    # the background jobs' timing and last outcomes
    def jobs():
        return {"ok": True, "profile": profile.current().value, "jobs": background.JOBS.snapshot(), "generated": now_iso()}

    @r.get("/api/debug/ai", responses=answers(AiCosts))      # what AI cost each local day, by feature
    def ai(args: Annotated[DaysArgs, Query()]):
        days = max(1, min(366, args.days))
        return {"ok": True, "days": assistant.calls.usage(days), "governor": assistant.governor().snapshot(),
                "batch": assistant.assessments.batch_status(), "generated": now_iso()}

    return r
