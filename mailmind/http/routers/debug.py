"""What the app says of its own running: the connection pool, the background jobs, and what AI cost."""
from mailmind.http.routing import Routes
from mailmind.kernel.when import now_iso
from mailmind.platform import profile


def routes(mailbox, background, assistant):
    r = Routes()

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
