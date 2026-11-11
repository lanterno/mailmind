"""The AI's pass over the Screener: only what it is sure of is said, it names only who it was shown, and it writes no rule."""
import pytest

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.assistant.domain import screening
from mailmind.bootstrap import assistant
from mailmind.bootstrap.curation import rules


def said(n=1, verdict="keep", confidence=0.95, why="a shop you use", **more):
    return {"id": n, "verdict": verdict, "confidence": confidence, "why": why, **more}


# ── the pass, on a person's click ────────────────────────────────────────────────────────────────

def waiting(app):
    return [i["from_addr"] for i in app.get(f"/api/screener?account={w.ADA}").json["items"]]


def test_a_pass_reads_who_is_waiting_and_suggests_for_those_it_is_sure_of(app, world, ai):
    who = waiting(app)
    ai.queue(fa.answer({"verdicts": [said(1, "block", 0.9, "bulk mail"), said(2, "keep", 0.5, "maybe")]}))
    r = app.post("/api/screener/suggest", {"account": w.ADA})
    assert (r.status, r.json["ok"], r.json["account"], r.json["asked"]) == (200, True, w.ADA, len(who))
    assert r.json["suggestions"] == [{"from_addr": who[0], "verdict": "block", "confidence": 0.9, "why": "bulk mail"}]
    assert "[1] " in ai.requests[0].prompt and who[0] in ai.requests[0].prompt
    assert ai.requests[0].body["max_tokens"]


def test_a_pass_never_writes_a_rule(app, world, ai):
    before = rules.load()
    ai.queue(fa.answer({"verdicts": [said(1, "block", 1.0, "bulk mail"), said(2, "keep", 1.0, "known")]}))
    assert app.post("/api/screener/suggest", {"account": w.ADA}).status == 200
    assert rules.load() == before


def test_a_pass_is_counted_under_its_own_feature(app, world, ai):
    app.post("/api/screener/suggest", {"account": w.ADA})
    assert assistant.calls.usage(1)[0]["features"]["screen"]["calls"] == 1


def test_a_pass_reads_no_more_than_its_senders(app, world, ai, monkeypatch):
    many = [{"from_addr": f"s{i}@example.test", "from_name": f"S{i}", "count": 1} for i in range(screening.MAX_SENDERS + 10)]
    monkeypatch.setattr(assistant._mail, "screener_items", lambda account: many)
    r = app.post("/api/screener/suggest", {"account": w.ADA})
    assert r.json["asked"] == screening.MAX_SENDERS
    assert f"[{screening.MAX_SENDERS + 1}]" not in ai.requests[0].prompt


@pytest.mark.parametrize("body", [{}, {"account": "nobody@example.test"}])
def test_with_no_one_waiting_nothing_is_sent(app, world, ai, body):
    r = app.post("/api/screener/suggest", body)
    assert (r.status, r.json["asked"], r.json["suggestions"], ai.requests) == (200, 0, [], [])


def test_a_get_spends_nothing(app, world, ai):
    assert app.get("/api/screener/suggest").status in (404, 405)
    assert ai.requests == []
