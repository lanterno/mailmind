"""What the app says of its own running: the connection pool, the background jobs, what AI cost, and all
of that at a glance."""
from mailmind.http.routing import Routes
from mailmind.kernel.when import now_iso
from mailmind.platform import profile


def routes(mailbox, compose, background, assistant):
    r = Routes()

    @r.get("/api/debug/health")  # read-only, and it never counts as someone here: ids and counts, never an address
    def health(req):
        idle, governor = mailbox.realtime.watching(), assistant.governor().snapshot()
        return {"ok": True, "profile": profile.current().value, "generated": now_iso(),
                "jobs": background.JOBS.snapshot(),
                "idle": {"accounts": len(idle), "running": sum(idle.values())},
                "queues": {"outbox": compose.outbox.QUEUE.counts(), "commands": mailbox.commands.QUEUE.counts(),
                           "drafts_waiting": len(compose.drafts.waiting())},
                "breakers": governor["breaker"],
                "ai": {"today": assistant.calls.usage(1)[0], "limit_micros": governor["limit_micros"],
                       "batch": assistant.triage.batch_status()}}

    @r.get("/api/debug/pool")
    def pool(req):
        return {"ok": True, **mailbox.pool.stats()}

    @r.get("/api/debug/jobs")    # the background jobs' timing and last outcomes
    def jobs(req):
        return {"ok": True, "profile": profile.current().value, "jobs": background.JOBS.snapshot(), "generated": now_iso()}

    @r.get("/api/debug/ai")      # what AI cost each local day, by feature
    def ai(req):
        days = max(1, min(366, int(req.arg("days", "30"))))
        return {"ok": True, "days": assistant.calls.usage(days), "governor": assistant.governor().snapshot(),
                "batch": assistant.triage.batch_status(), "generated": now_iso()}

    return r
