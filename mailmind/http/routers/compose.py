"""What the page asks of Compose: a send, a draft and the outbox."""
from mailmind.http.routing import Routes
from mailmind.platform.logging import get_logger

log = get_logger("send")
resume_log = get_logger("resume")


def routes(compose):
    r = Routes()

    @r.get("/api/outbox")
    def outbox(req):
        return {**compose.outbox.list_recent(int(req.arg("limit", "50"))), "drafts": compose.drafts.waiting()}

    @r.post("/api/send")
    def send(req):
        try:
            return compose.sending.send(req.body)
        except ValueError as ve:           # our own validation messages are secret-free
            return {"ok": False, "error": str(ve)}, 400
        except Exception as ex:            # never leak a raw exception on the send route
            log.error("error", exc_info=ex)
            return {"ok": False, "error": "send failed"}, 500

    @r.post("/api/outbox/retry")
    def outbox_retry(req):
        answer = compose.outbox.retry(int(req.body.get("id")))
        return answer, 200 if answer["ok"] else 404

    @r.post("/api/outbox/cancel")
    def outbox_cancel(req):
        return {"ok": compose.outbox.cancel(int(req.body.get("id")))}

    @r.post("/api/outbox/sendnow")
    def outbox_send_now(req):
        ok = compose.outbox.send_now(int(req.body.get("id")))
        return {"ok": ok}, 200 if ok else 404

    @r.post("/api/draft")
    def draft(req):
        try:
            return compose.drafts.save(req.body)
        except ValueError as ve:
            return {"ok": False, "error": str(ve)}, 400

    @r.post("/api/draft/resume")      # read-only: a saved draft, back into the composer
    def draft_resume(req):
        try:
            return compose.drafts.resume(req.body)
        except Exception as ex:
            resume_log.error("error", exc_info=ex)
            return {"ok": False, "error": "Couldn’t open this draft."}, 500

    return r
