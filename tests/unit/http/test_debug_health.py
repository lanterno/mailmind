"""/api/debug/health: how the app is doing at a glance. It shows ids and counts, and it changes nothing."""
import json
from datetime import timedelta

from fakes import anthropic_api as fa
from mailmind.bootstrap import assistant, background, mailbox
from mailmind.platform.workers.scheduler import Scheduler

REWRITE = {"op": "shorten", "text": "see you thursday"}


def test_with_nothing_going_on_it_shows_nothing_going_on(app, world):
    r = app.get("/api/debug/health")
    assert r.status == 200
    assert (r.json["ok"], r.json["profile"]) == (True, "full")
    assert r.json["generated"] == "2026-11-02T12:00:00+00:00"
    assert r.json["jobs"] == []
    assert r.json["idle"] == {"accounts": 0, "running": 0}
    assert r.json["queues"] == {"outbox": {}, "commands": {}, "drafts_waiting": 0}
    assert r.json["breakers"] == []
    assert r.json["ai"] == {"today": {"day": "2026-11-02", "calls": 0, "cost_micros": 0, "features": {}},
                            "limit_micros": 15_000_000,
                            "batch": {"waiting": 0, "state": None, "estimate_micros": 0}}


def test_it_shows_a_job_that_failed_by_the_class_of_its_error(app, monkeypatch):
    def refused():
        raise ConnectionResetError("the server said: no, ada@example.test")
    jobs = Scheduler()
    jobs.add("sync", refused, every=300)
    jobs.run("sync")
    monkeypatch.setattr(background, "JOBS", jobs)
    r = app.get("/api/debug/health")
    [job] = r.json["jobs"]
    assert (job["name"], job["failures"], job["last_error"]) == ("sync", 1, "ConnectionResetError")
    assert "example.test" not in json.dumps(r.json)


def test_it_counts_idles_workers_without_naming_their_accounts(app, monkeypatch):
    monkeypatch.setattr(mailbox.realtime, "watching", lambda: {"ada@example.test": True, "bo@example.test": False})
    r = app.get("/api/debug/health")
    assert r.json["idle"] == {"accounts": 2, "running": 1}
    assert "example.test" not in json.dumps(r.json)


def test_it_counts_what_waits_in_each_queue_by_its_state(app, world, monkeypatch):
    monkeypatch.setenv("MAILMIND_PROFILE", "read-only")          # so the archive waits in its queue
    assert app.post("/api/archive", {"pk": world.pk("ada-inbox-05-receipt.eml")}).json["status"] == "queued"
    r = app.get("/api/debug/health")
    assert r.json["profile"] == "read-only"
    assert r.json["queues"]["commands"] == {"queued": 1}
    assert "example.test" not in json.dumps(r.json)


def test_it_shows_an_open_breaker_and_what_ai_cost_today(app, world, ai):
    ai.responder = lambda req: fa.error(401, "authentication_error", "invalid x-api-key")
    app.post("/api/ai/rewrite", REWRITE)
    r = app.get("/api/debug/health")
    assert r.json["breakers"] == ["every feature"]
    ai.responder = None
    assert app.post("/api/ai/rewrite", REWRITE).status == 200
    today = app.get("/api/debug/health").json["ai"]["today"]
    assert today["calls"] == 2 and today["features"]["rewrite"]["calls"] == 2


def test_asking_doesnt_count_as_someone_being_here(app):
    app.clock.shift(timedelta(minutes=5))
    assert app.get("/api/debug/health").status == 200
    assert assistant.governor().snapshot()["since_seen"] == 300.0


def test_it_answers_only_a_get_from_the_page_itself(app):
    assert app.get("/api/debug/health", headers={"Sec-Fetch-Site": "cross-site"}).status == 403
    assert app.post("/api/debug/health").status == 404
