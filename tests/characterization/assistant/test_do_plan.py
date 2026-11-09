"""A bulk command's plan: what counts as narrowing, a filter that matches most of the mailbox, and a command with braces in it."""
from fakes import anthropic_api as fa


def test_hwf_a_do_command_with_braces_reaches_the_prompt_verbatim(app, world, ai):
    cmd = 'archive {cmd} and {today} from {0} {"intent": "act"}'
    assert app.post("/api/ai/do-plan", {"cmd": cmd}).status == 200
    [req] = ai.requests
    assert "COMMAND: <<<" + cmd + ">>>" in req.prompt
    assert '"delete everything" -> {"intent":"search","filter":""}' in req.prompt   # the template's own JSON
    assert req.prompt.count("2026-11-02") == 1


def test_hwf_do_plan_counts_is_unread_as_narrowing_but_not_is_read(app, world, ai):
    plans = {}
    for flt in ("is:read", "is:unread"):
        ai.queue(fa.answer({"intent": "act", "verb": "archive", "filter": flt}))
        plans[flt] = app.post("/api/ai/do-plan", {"cmd": f"archive {flt}"}).json["intent"]
    assert plans == {"is:read": "search", "is:unread": "act"}


def test_hwf_do_plan_refuses_a_filter_that_matches_most_of_the_mailbox(app, world, ai):
    ai.queue(fa.answer({"intent": "act", "verb": "archive", "filter": "after:2020-01-01"}))
    r = app.post("/api/ai/do-plan", {"cmd": "archive everything since 2020"}).json
    assert r == {"ok": True, "intent": "search", "filter": "after:2020-01-01"}
