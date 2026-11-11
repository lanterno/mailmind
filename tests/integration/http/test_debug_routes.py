"""The debug routes on their router: how `days` reads, what a pool in use shows, who counts as someone here. What each says is held
where it's made (test_debug_health, test_scheduler, test_ai_ledger, test_governor), and by the contract in every one of those."""
import json
from datetime import timedelta

import pytest

import problems
from fixtures import world as w
from mailmind.bootstrap import accounts, assistant, mailbox

BAD_REQUEST = problems.BAD_REQUEST
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
    assert (r.json["ok"], r.json["enabled"], r.json["target"], r.json["counters"]["created"]) == (True, True, "listed", 1)
    [slot] = r.json["slots"]
    assert slot["open"] is True and slot["uses"] == 1
    assert "example.test" not in json.dumps(r.json)
    mailbox.pool.close_all()


@pytest.mark.parametrize("given, target", [("1", "all"), ("all", "all"), (w.ADA, "listed"), ("", "")])
def test_the_pool_says_how_it_was_set_and_never_who(app, monkeypatch, given, target):
    monkeypatch.setenv("MAILMIND_POOL", given)
    answer = app.get("/api/debug/pool").json
    assert (answer["target"], answer["enabled"]) == (target, bool(target))
    assert "example.test" not in json.dumps(answer)


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
    assert (r.status, r.json) == (404, problems.NO_ROUTE)


@pytest.mark.parametrize("path", NOTICED)
def test_asking_any_but_the_health_counts_as_someone_here_as_it_did(app, path):
    """test_debug_health holds that asking the health doesn't."""
    app.clock.shift(timedelta(minutes=5))
    assert app.get(path).status == 200
    assert assistant.governor().snapshot()["since_seen"] == 0.0
