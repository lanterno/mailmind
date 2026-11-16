"""AI's switch and its choice of provider, through the app (#49): turned off in Settings, nothing is sent anywhere, by a route
or by any background job, whatever key there is; turned on again it is sent at once. Chosen, another provider is asked in
Claude's place by the same jobs, and a backlog never goes as a batch, which only Claude takes."""
import pytest

import problems
from fakes import anthropic_api as fa
from fakes import openai_api as fo
from mailmind.bootstrap import assistant, background

LUNCH = "ada-inbox-02-lunch-confirm.eml"
SPENDS = [("/api/ai/summarize", lambda world: world.copy(LUNCH)), ("/api/ai/rewrite", lambda world: {"op": "shorten", "text": "hi"}),
          ("/api/ai/ask", lambda world: {"question": "When is lunch with Lin?"}),
          ("/api/ai/draft-reply", lambda world: {**world.copy(LUNCH), "tone": "warm"})]


@pytest.fixture
def off(app, world, ai):
    """A key the fake accepts, every job that asks on its own turned on, and AI turned off."""
    assert app.post("/api/preferences", {"ai_enabled": False, "morning_brief": True, "weekly_review": True,
                                         "auto_tidy": True}).status == 200
    return app


@pytest.mark.parametrize("path, body", SPENDS, ids=[p for p, _ in SPENDS])
def test_turned_off_a_route_says_ai_is_off_and_sends_nothing(off, world, ai, path, body):
    r = off.post(path, body(world))
    assert (r.status, r.json) == (409, problems.ai_off("turned-off"))
    assert ai.requests == []


def test_turned_off_no_background_job_sends_anything(off, ai):
    assert assistant.assessments.round() is None
    assert assistant.assessments.batch_submit(at_least=1, keep=0) == 0
    background.brief_ahead_round()
    background.review_ahead_round()
    background.tidy_round()
    assistant.drafting.rounds()()
    assert ai.requests == []
    st = off.get("/api/digest").json["ai"]
    assert (st["engine"], st["provider"], st["off"]) == ("none", "anthropic", "turned-off")


def test_turned_on_again_it_is_sent_at_once(off, world, ai):
    assert off.post("/api/preferences", {"ai_enabled": True}).status == 200
    assert off.post("/api/ai/summarize", world.copy(LUNCH)).status == 200
    assert [r.path for r in ai.requests] == ["/v1/messages"]
    assert off.get("/api/digest").json["ai"]["off"] is None


@pytest.fixture
def openai(app, world, ai, openai_api, fake_keychain, monkeypatch):
    """OpenAI chosen, reached at the fake, with its key kept from Settings; Claude's key still there, unasked."""
    monkeypatch.setattr(assistant.calls, "MODEL", None)
    assert app.post("/api/preferences", {"ai_provider": "openai", "ai_base_url": openai_api.url}).status == 200
    assert app.post("/api/ai/key", {"provider": "openai", "key": fo.KEY}).status == 200
    return openai_api


def test_another_provider_is_asked_in_claudes_place_by_the_jobs_too(app, openai, ai):
    assert assistant.assessments.summarize_pending(limit=3, workers=1) == 3
    assert len(openai.chats) == 3 and ai.requests == []
    assert assistant.calls.usage(1)[0]["features"]["triage"]["calls"] == 3
    st = app.get("/api/digest").json["ai"]
    assert (st["engine"], st["provider"], st["model"], st["off"]) == ("api", "openai", "gpt-6.1-sol", None)


def test_a_backlog_never_goes_as_a_batch_to_another_provider(app, openai, ai, monkeypatch):
    monkeypatch.setattr(assistant.assessments, "BATCH_POLL", 0)
    assert assistant.assessments.batch_submit(at_least=1, keep=0) == 0
    assert assistant.assessments.batch_collect() == 0
    assert not [r for r in ai.requests if r.path.startswith(fa.BATCHES)]


def test_a_batch_sent_to_claude_waits_while_another_provider_is_chosen(app, world, ai, openai_api, fake_keychain, monkeypatch):
    monkeypatch.setattr(assistant.calls, "MODEL", None)
    monkeypatch.setattr(assistant.assessments, "BATCH_POLL", 0)
    sent = assistant.assessments.batch_submit(at_least=1, keep=0)
    assert sent > 0
    ai.end_batch()
    assert app.post("/api/preferences", {"ai_provider": "openai", "ai_base_url": openai_api.url}).status == 200
    assert app.post("/api/ai/key", {"provider": "openai", "key": fo.KEY}).status == 200
    assert assistant.assessments.batch_collect() == 0
    assert app.post("/api/preferences", {"ai_provider": "anthropic"}).status == 200
    assert assistant.assessments.batch_collect() == sent
