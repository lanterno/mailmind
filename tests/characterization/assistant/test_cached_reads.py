"""The AI reads that are kept: the brief, the delta, the review and a thread's summary. What a failed call and an unusable
reply leave behind, what two first loads pay, when a day ends, and that a GET the page didn't make spends nothing."""
import os
import threading
import time
from datetime import timedelta

import pytest

from fakes import anthropic_api as fa
from mailmind.bootstrap.assistant import answers
from assistant_world import LUNCH, summarize_as

AI_READS = {
    "brief": lambda app, world: app.post("/api/briefing", {}),
    "delta": lambda app, world: app.post("/api/briefing/since", {"since": 0}),
    "review": lambda app, world: app.post("/api/review", {}),
    "thread summary": lambda app, world: app.post("/api/ai/summarize", world.copy(LUNCH)),
}
UNUSABLE = {"brief": fa.answer({}), "delta": fa.answer({}), "review": fa.answer({}),
            "thread summary": fa.answer("Sorry, there is no JSON in this reply.")}
FALLBACK = {
    "brief": lambda r: r["model"] == "fallback",
    "delta": lambda r: r["headline"].endswith(" new since you last looked."),
    "review": lambda r: r["model"] == "fallback",
    "thread summary": lambda r: r["model"] == "parse-error",
}


@pytest.mark.parametrize("which", list(AI_READS))
def test_hwf_a_failed_call_is_retried_but_an_unusable_reply_is_cached_once(app, world, ai, which):
    summarize_as(world.pk(LUNCH), 5, "action-needed", "Reply")      # the brief needs a triaged message
    ai.queue(fa.error(400, "invalid_request_error", "bad request"), UNUSABLE[which])
    assert AI_READS[which](app, world).status == 502                # nothing cached: the next load asks again
    retried = AI_READS[which](app, world)
    assert retried.status == 200 and FALLBACK[which](retried.json)
    again = AI_READS[which](app, world)
    assert again.status == 200 and again.json["cached"] is True and len(ai.requests) == 2


@pytest.mark.parametrize("route", ["/api/briefing", "/api/review"])
def test_hwf_the_daily_ai_cards_roll_at_local_midnight(app, world, ai, route):
    summarize_as(world.pk(LUNCH), 5, "action-needed", "Reply")
    os.environ["TZ"] = "XXX-10"           # UTC+10: the frozen noon UTC is 22:00 here
    time.tzset()
    try:
        assert app.post(route, {}).status == 200
        app.clock.shift(timedelta(hours=1))           # 23:00 local
        app.post(route, {})
        assert len(ai.requests) == 1
        app.clock.shift(timedelta(hours=1))           # local midnight, though UTC is still on the same day
        app.post(route, {})
        assert len(ai.requests) == 2
    finally:
        os.environ["TZ"] = "UTC"
        time.tzset()


CACHED_READS = {     # each cached AI read, and the store read that finds its cache
    "brief": "get_brief", "delta": "get_delta", "review": "get_review",
    "thread summary": "get_thread_summary",
}


@pytest.mark.parametrize("which", list(CACHED_READS))
def test_two_first_loads_of_a_cached_ai_read_pay_once(app, world, ai, monkeypatch, which):
    summarize_as(world.pk(LUNCH), 5, "action-needed", "Reply")
    real = getattr(answers, CACHED_READS[which])
    reads, held, go = [], threading.Event(), threading.Event()

    def slow_first_read(*a, **kw):
        row = real(*a, **kw)
        reads.append(row)
        if len(reads) == 1:                       # the first load found no cache: hold it there
            held.set()
            go.wait(5)
        return row
    monkeypatch.setattr(answers, CACHED_READS[which], slow_first_read)
    first = threading.Thread(target=AI_READS[which], args=(app, world))
    first.start()
    held.wait(5)
    second = AI_READS[which](app, world)          # meanwhile a second load computes and caches it
    go.set()
    first.join(10)
    assert second.status == 200 and not first.is_alive()
    assert len(ai.requests) == 1


def test_an_ai_spending_get_the_page_didnt_make_spends_nothing(app, world, ai):
    """These GETs only read what is stored, and only the page's own same-origin request may reach
    them: not one that says nothing of where it came from."""
    summarize_as(world.pk(LUNCH), 5, "action-needed", "Reply")
    for path in ("/api/briefing", "/api/review", "/api/briefing/since?since=0"):
        app.get(path, headers={"X-CSRF-Token": None, "Sec-Fetch-Site": None})
    assert [r.prompt[:40] for r in ai.requests] == []
