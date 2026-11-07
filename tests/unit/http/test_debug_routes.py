"""The debug routes on their router: how `days` reads, what a pool in use shows, who counts as someone here. What each says is held
where it's made (test_debug_health, test_scheduler, test_ai_ledger, test_governor), and by the contract in every one of those."""
import json
from datetime import timedelta

import pytest

from fixtures import world as w
from mailmind.bootstrap import accounts, assistant, mailbox

BAD_REQUEST = {"ok": False, "error": "bad request"}
QUIET = "/api/debug/health"
NOTICED = ["/api/debug/pool", "/api/debug/jobs", "/api/debug/ai"]


@pytest.mark.parametrize("query, days", [("", 30), ("?days=", 30), ("?days=2", 2), ("?days=2&days=9", 2), ("?days=%202", 2), ("?days=0", 1), ("?days=-3", 1),
                                         ("?days=5000", 366), ("?other=x", 30)])
def test_days_reads_as_it_did(app, query, days):
    r = app.get(f"/api/debug/ai{query}")
    assert (r.status, len(r.json["days"])) == (200, days)


@pytest.mark.parametrize("query", ["?days=x", "?days=2.5", "?days=99999999999999999999999", "?days=x&days=2"])
def test_days_that_wont_read_is_the_400(app, query):
    r = app.get(f"/api/debug/ai{query}")
    assert (r.status, r.json) == (400, BAD_REQUEST)


def test_a_pool_in_use_shows_its_slots(app, monkeypatch):
    """By name, since "all" leaves out a loopback server, as Bridge is and each of the world's is."""
    monkeypatch.setenv("MAILMIND_POOL", w.ADA)
    with mailbox.pool.borrow(accounts.get(w.ADA)):
        pass
    r = app.get("/api/debug/pool")
    assert (r.json["ok"], r.json["enabled"], r.json["target"], r.json["counters"]["created"]) == (True, True, w.ADA, 1)
    assert r.json["slots"][w.ADA]["open"] is True and r.json["slots"][w.ADA]["uses"] == 1
    mailbox.pool.close_all()


def test_a_ledger_that_cant_be_read_shows_as_no_spending_known(app, monkeypatch):
    def unreadable():
        raise OSError("no such table")
    monkeypatch.setattr(assistant.governor(), "_spent", unreadable)
    assert app.get("/api/debug/ai").json["governor"]["spent_today_micros"] is None


@pytest.mark.parametrize("path", [QUIET, *NOTICED])
def test_each_answers_in_ids_and_counts_and_never_an_address(app, world, path):
    r = app.get(path)
    assert (r.status, r.json["ok"]) == (200, True)
    assert "example.test" not in json.dumps(r.json)


@pytest.mark.parametrize("path", [QUIET, *NOTICED])
def test_each_answers_only_a_get(app, path):
    r = app.post(path, {})
    assert (r.status, r.body) == (404, b"not found")


@pytest.mark.parametrize("path", NOTICED)
def test_asking_any_but_the_health_counts_as_someone_here_as_it_did(app, path):
    """test_debug_health holds that asking the health doesn't."""
    app.clock.shift(timedelta(minutes=5))
    assert app.get(path).status == 200
    assert assistant.governor().snapshot()["since_seen"] == 0.0
