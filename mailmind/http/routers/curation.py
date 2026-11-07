"""What the page asks of Curation: the rules, the Screener, a sender's cleanup, sweeps and the audit trail."""
from mailmind.http.routing import Routes
from mailmind.kernel.errors import Problem
from mailmind.platform.logging import get_logger

log = get_logger("cleanup")


def routes(curation):
    r = Routes()

    @r.get("/api/rules")
    def rules(req):
        return {"rules": curation.rules.load()}

    @r.post("/api/rules")
    def rule(req):
        return curation.screener.rule(req.body)

    @r.get("/api/audit")
    def audit(req):
        return curation.AUDIT.read(req.arg("kind", "all"), req.int_arg("limit", 120))

    @r.get("/api/screener")
    def screener(req):
        return curation.screener.queue(req.arg("account"))

    @r.get("/api/cleanup/senders")
    def cleanup_senders(req):
        return curation.cleanup.senders(req.query)

    @r.get("/api/cleanup/sender")
    def cleanup_sender(req):
        return curation.cleanup.sender(req.query)

    @r.post("/api/cleanup/execute")
    def cleanup_execute(req):
        try:
            return curation.cleanup.execute(req.body)
        except Problem:
            raise
        except Exception as ex:
            log.error("error", exc_info=ex)
            return {"ok": False, "error": "cleanup failed"}, 500

    @r.post("/api/sweep")
    def sweep(req):
        return curation.sweeps.run(req.body.get("account", "all"), bool(req.body.get("dry_run", False)))

    return r
