"""What the app says of its own running, for its owner (`mailmind.http.routers.debug`): what AI cost each day with the cost
governor and the batch out (`/api/debug/ai`), the background jobs (`/api/debug/jobs`), and all of that at a glance with
the queues (`/api/debug/health`). Ids and counts only, never an address.

The served suites start no workers, so neither server has a job to show, and the clock is frozen, so what AI cost lands
on known days and the governor's seconds come out the same. Asking how the app is doing never counts as someone being
here: drafting a reply ahead, which waits for someone, shows it. The golden files are in tests/golden/http_debug/.
"""
import json
from datetime import timedelta
from urllib.parse import urlencode

import pytest

from fakes import anthropic_api as fa
from fixtures import world as w

PATHS = ["/api/debug/health", "/api/debug/jobs", "/api/debug/ai"]
REWRITE = {"op": "shorten", "text": "see you thursday"}
NOW_TS = 1_793_620_800          # conftest.NOW, as the server's clock counts it

# What the ledger holds, as (day, feature, model, served, outcome, calls, cost in micro-dollars): two rows of one
# feature on a day add up, a day long gone is listed only when asked for, and one after today, which a clock set back
# would leave, never is.
LEDGER = [
    ("2026-11-03", "triage", "claude-opus-5-5", "claude-opus-5-5", "ok", 7, 70_000),
    ("2026-11-02", "triage", "claude-opus-5-5", "claude-opus-5-5", "ok", 3, 9_000),
    ("2026-11-02", "triage", "claude-opus-5-5", "", "failed", 1, 0),
    ("2026-11-02", "rewrite", "claude-opus-5-5", "claude-opus-5-5", "ok", 2, 6_000),
    ("2026-11-02", "ask", "claude-opus-5-5", "claude-sonnet-5-5", "ok", 1, 4_000),
    ("2026-11-01", "brief", "claude-opus-5-5", "claude-opus-5-5", "ok", 1, 20_000),
    ("2026-10-30", "triage-batch", "claude-opus-5-5", "claude-opus-5-5", "ok", 40, 50_000),
    ("2026-09-20", "thread", "claude-opus-5-5", "claude-opus-5-5", "ok", 5, 15_000),
]


def stored(app, *statements):
    """Rows put in the database while the server is stopped, as another process would have left them."""
    from mailmind.bootstrap.mailbox import store
    with app.stopped():
        c = store.conn()
        try:
            for sql, args in statements:
                c.execute(sql, args)
            c.commit()
        finally:
            c.close()


def no_address(r):
    assert "example.test" not in json.dumps(r.json)
    return r


def test_what_ai_cost_each_day_with_the_governor_and_the_batch(app, golden, world):
    """A batch out books what it may cost against background work, and holds its messages."""
    stored(app, *[("INSERT INTO ai_usage(day, feature, model, served, outcome, calls, input_tokens, output_tokens, "
                   "cache_write_tokens, cache_read_tokens, cost_micros, last_ts) VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, 0, ?, ?)",
                   (*row, NOW_TS)) for row in LEDGER],
           ("INSERT INTO ai_batches(id, remote_id, feature, model, state, requests, estimate_micros, cutoff_ts, "
            "created_at, prompt_version) VALUES (1, 'msgbatch_out', 'triage-batch', 'claude-opus-5-5', 'running', 2, "
            "12000, 0, ?, 2)", (NOW_TS - 600,)),
           ("INSERT INTO ai_batch_items(message_key, batch_id) SELECT message_key, 1 FROM messages "
            "WHERE message_key IS NOT NULL ORDER BY message_key LIMIT 2", ()))
    golden.record("the last four days", no_address(app.get("/api/debug/ai?days=4")))
    golden.record("today, at a glance", no_address(app.get("/api/debug/health")))
    month = app.get("/api/debug/ai").json["days"]
    golden.note("thirty days unless asked: the first and the last", [month[0]["day"], month[-1]["day"]])
    golden.note("sixty days: the oldest with calls", [d for d in app.get("/api/debug/ai?days=60").json["days"]
                                                      if d["calls"]][-1])


DAYS = [("", 30), ("?days=", 30), ("?days=2", 2), ("?days=2&days=9", 2), ("?days=%202", 2), ("?days=0", 1),
        ("?days=-3", 1), ("?days=366", 366), ("?days=5000", 366), ("?other=x", 30)]


@pytest.mark.parametrize("query, days", DAYS, ids=[query or "unasked" for query, _ in DAYS])
def test_days_is_held_to_one_to_a_year(app, query, days):
    r = app.get(f"/api/debug/ai{query}")
    assert (r.status, len(r.json["days"])) == (200, days)
    assert r.json["days"][0]["day"] == "2026-11-02"


@pytest.mark.parametrize("query", ["?days=x", "?days=2.5", "?days=99999999999999999999999", "?days=x&days=2"])
def test_days_that_wont_read_as_a_number_is_refused(app, golden, query):
    golden.record(f"/api/debug/ai{query}", app.get(f"/api/debug/ai{query}"))


def test_the_governor_says_how_long_since_a_click_and_where_the_breaker_is_open(app, golden, ai):
    """A refused key opens the breaker for every background call; a click still goes, and is counted. Asking the AI
    route counts as someone being here, as any request does but the health's."""
    golden.record("before any call", app.get("/api/debug/ai?days=1"))
    ai.responder = lambda req: fa.error(401, "authentication_error", "invalid x-api-key")
    app.post("/api/ai/rewrite", REWRITE)
    app.clock.shift(timedelta(seconds=30))
    golden.record("a refused key, thirty seconds on", no_address(app.get("/api/debug/ai?days=1")))
    golden.record("at a glance", no_address(app.get("/api/debug/health")))
    ai.responder = None
    assert app.post("/api/ai/rewrite", REWRITE).status == 200
    app.clock.shift(timedelta(minutes=2, seconds=3, milliseconds=450))
    golden.record("a good call, two minutes on", app.get("/api/debug/ai?days=1"))


def send(app):
    return app.post("/api/send", {"account": w.ADA, "mode": "new", "to": ["Lin Mori <lin@example.test>"],
                                  "subject": "Fabric order", "text": "Hi Lin,\n\nWant half?\n\nAda",
                                  "idempotency_key": "k-debug"})


def autosave(app):
    return app.post("/api/draft", {"account": w.ADA, "mode": "new", "to": ["Lin Mori <lin@example.test>"],
                                   "subject": "Second thoughts", "text": "Hi Lin,", "autosave": True})


def queues(app):
    return no_address(app.get("/api/debug/health")).json["queues"]


def test_the_health_counts_what_waits_in_each_queue_by_its_state(app, golden, world):
    """No worker runs here: a send waits in the outbox until the Undo Send window has passed and a pass takes it, and an
    autosave's push is due a minute on, and named half a minute after that until the push job takes it. A change to the
    mail is carried at once."""
    golden.record("with nothing going on", no_address(app.get("/api/debug/health")))
    assert app.post("/api/archive", world.copy("ada-inbox-05-receipt.eml")).json["ok"]
    assert send(app).json["ok"]
    saved = autosave(app)
    assert saved.json["ok"] and not saved.json["pushed"]
    golden.record("a change, a send and an autosave", no_address(app.get("/api/debug/health")))
    app.clock.shift(timedelta(seconds=89))
    golden.note("89 seconds on", queues(app))
    app.clock.shift(timedelta(seconds=1))
    golden.record("90 seconds on", no_address(app.get("/api/debug/health")))
    app.work("outbox")
    app.work("drafts")
    golden.record("once a pass of each has run", no_address(app.get("/api/debug/health")))


def test_read_only_a_change_waits_in_its_queue_and_a_save_is_named_once_due(app, golden, world):
    """Read-only, nothing reaches a mail server: a change waits for a full run, and with no push job to wait for, a save
    is named as soon as its push is due."""
    app.env["MAILMIND_PROFILE"] = "read-only"
    app.restart()
    assert app.post("/api/archive", world.copy("ada-inbox-05-receipt.eml")).json["status"] == "queued"
    assert send(app).json["ok"]
    assert autosave(app).json["ok"]
    golden.record("at a glance", no_address(app.get("/api/debug/health")))
    app.clock.shift(timedelta(seconds=59))
    golden.note("59 seconds on", queues(app))
    app.clock.shift(timedelta(seconds=1))
    golden.note("a minute on", queues(app))


def test_asking_how_the_app_is_doing_never_counts_as_someone_being_here(app, world, ai):
    """A reply drafted ahead waits for someone here in the last 45 minutes, and the draft-ahead route, which a hidden tab
    asks on its own, doesn't count as someone either. So after an hour away, asking the health drafts nothing; asking
    the jobs, as any other request, does."""
    ai.responder = lambda req: (fa.answer({"summary": "Wants an answer.", "importance": 5,
                                           "category": "action-needed", "action": "Reply today"})
                                if req.prompt.startswith("You are triaging ONE email") else None)
    app.work("assess")
    ai.requests.clear()
    ahead = "/api/ai/draft-ahead?" + urlencode(world.copy("cy-inbox-02-thread-end.eml"))
    app.clock.shift(timedelta(hours=1))
    assert app.get("/api/debug/health").status == 200
    assert app.get(ahead).json["status"] == "generating"
    app.settle()
    assert ai.requests == []
    assert app.get("/api/debug/jobs").status == 200
    assert app.get(ahead).json["status"] == "generating"
    app.settle()
    assert len(ai.requests) == 1
    assert app.get(ahead).json["status"] == "ready"


def test_the_jobs_none_runs_here_and_the_profile(app, golden):
    golden.record("the jobs", no_address(app.get("/api/debug/jobs")))
    app.env["MAILMIND_PROFILE"] = "read-only"
    app.restart()
    golden.record("the jobs, read-only", app.get("/api/debug/jobs"))
    golden.note("the health's profile, read-only", app.get("/api/debug/health").json["profile"])


@pytest.mark.parametrize("path", PATHS)
def test_each_answers_only_a_get_from_the_page_itself(app, golden, path):
    golden.record("a POST", app.post(path, {}))
    golden.record("a cross-site GET", app.get(path, headers={"Sec-Fetch-Site": "cross-site"}))
