"""What the page asks of the Assistant. A route that spends is a POST, and the GETs read what is stored; the one GET that
can start a call is drafting ahead's kick, which needs the page's token."""
from mailmind.assistant.api import AIOff, OverLimit
from mailmind.http.routing import Routes
from mailmind.platform import perimeter


def _scrubbed(what, failed, answer):
    """A route's answer, with a failure logged and answered in general terms. The two the handler answers go through: the
    402 (the page asks) and the 409."""
    def route(req):
        try:
            return answer(req)
        except (OverLimit, AIOff):
            raise
        except Exception as ex:
            print(f"[ai] {what}: {type(ex).__name__}: {ex}", flush=True)
            return {"ok": False, "error": failed}, 500
    return route


def routes(assistant):
    r = Routes()

    @r.get("/api/briefing")          # what is stored of the "Today" card; POST writes it
    def briefing(req):
        return assistant.asking.briefing(req.query, generate=False)

    @r.get("/api/review")            # what is stored of the Weekly Review; POST writes it
    def review(req):
        return assistant.asking.review(req.query, generate=False)

    @r.get("/api/briefing/since")    # what is stored of the "since you last looked" delta
    def delta(req):
        return assistant.asking.delta(req.query, generate=False)

    @r.get("/api/ai/draft-ahead")    # serve/peek; the generation KICK needs the CSRF token
    def draft_ahead(req):
        return assistant.drafting.get(req.query, kick_ok=perimeter.token_matches(req.handler.headers, req.handler.CSRF))

    r.post("/api/ai/summarize")(_scrubbed("error", "summary failed", lambda req: assistant.asking.summarize(req.body)))
    r.post("/api/ai/draft-reply")(_scrubbed("draft error", "draft failed", lambda req: assistant.drafting.reply(req.body)))
    r.post("/api/ai/rewrite")(_scrubbed("rewrite error", "rewrite failed", lambda req: assistant.asking.rewrite(req.body)))
    r.post("/api/ai/ask")(_scrubbed("ask error", "ask failed", lambda req: assistant.asking.ask(req.body)))

    @r.post("/api/ai/do-confirm")    # carry out the plan a preview froze
    def do_confirm(req):
        return assistant.bulk.confirm(req.body)

    # read-only: NL command → validated, capped action PREVIEW
    r.post("/api/ai/do-plan")(_scrubbed("do-plan error", "plan failed", lambda req: assistant.bulk.plan(req.body)))
    # write each once, if none is stored
    r.post("/api/briefing")(_scrubbed("briefing error", "briefing failed", lambda req: assistant.asking.briefing(req.body)))
    r.post("/api/review")(_scrubbed("review error", "review failed", lambda req: assistant.asking.review(req.body)))
    r.post("/api/briefing/since")(_scrubbed("delta error", "delta failed", lambda req: assistant.asking.delta(req.body)))
    # write each again
    r.post("/api/review/refresh")(_scrubbed("review error", "review failed", lambda req: assistant.asking.review(req.body, force=True)))
    r.post("/api/briefing/refresh")(_scrubbed("briefing error", "briefing failed", lambda req: assistant.asking.briefing(req.body, force=True)))

    return r
