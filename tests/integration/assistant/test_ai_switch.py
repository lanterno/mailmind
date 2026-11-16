"""AI's switch and its choice of provider, through the app (#49): turned off in Settings, nothing is sent anywhere, by a route
or by any background job, whatever key there is; turned on again it is sent at once. Chosen, another provider is asked in
Claude's place by the same jobs, and a backlog never goes as a batch, which only Claude takes: one out to Claude is let go,
and its mail goes to the provider chosen."""
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


def test_turned_off_no_key_is_looked_up_anywhere(off, ai, monkeypatch):
    """Not the Keychain, nor 1Password: a locked one asks nothing of the person while AI is off."""
    from mailmind.accounts.adapters import secrets
    assert off.post("/api/ai/key", {"provider": "anthropic", "key": "sk-ant-kept-while-off"}).status == 200
    looked, resolve = [], secrets.resolve
    monkeypatch.setattr(secrets, "resolve", lambda cred, **kw: looked.append(kw["who"]) or resolve(cred, **kw))
    assert off.get("/api/digest").json["ai"]["off"] == "turned-off"
    assert assistant.calls.engine() == "none" and assistant.engine().key == ""
    assistant.assessments.round()
    background.tidy_round()
    assert looked == []
    assert off.post("/api/preferences", {"ai_enabled": True}).status == 200
    assert off.get("/api/digest").json["ai"]["key_from"] == "mailmind.config.json"
    assert looked and set(looked) == {"anthropic_api_key"}


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


def test_the_claude_model_mailmind_model_names_is_asked_only_while_claude_is_the_provider(app, world, ai, openai_api,
                                                                                         fake_keychain, monkeypatch):
    monkeypatch.setattr(assistant.calls, "MODEL", "claude-haiku-5-5")
    assert app.post("/api/preferences", {"ai_model": "claude-sonnet-5-5"}).status == 200
    assert assistant.assessments.summarize_pending(limit=1, workers=1) == 1
    assert ai.requests[-1].body["model"] == "claude-haiku-5-5"
    assert app.get("/api/digest").json["ai"]["model"] == "claude-haiku-5-5"
    assert app.post("/api/preferences", {"ai_provider": "openai", "ai_base_url": openai_api.url}).status == 200
    assert app.post("/api/ai/key", {"provider": "openai", "key": fo.KEY}).status == 200
    assert assistant.assessments.summarize_pending(limit=1, workers=1) == 1
    assert app.post("/api/ai/rewrite", {"op": "shorten", "text": "see you thursday"}).status == 200
    assert [r.body["model"] for r in openai_api.chats] == ["gpt-6.1-sol"] * 2
    assert app.get("/api/digest").json["ai"]["model"] == "gpt-6.1-sol"


def test_a_backlog_never_goes_as_a_batch_to_another_provider(app, openai, ai, monkeypatch):
    monkeypatch.setattr(assistant.assessments, "BATCH_POLL", 0)
    assert assistant.assessments.batch_submit(at_least=1, keep=0) == 0
    assert assistant.assessments.batch_collect() == 0
    assert not [r for r in ai.requests if r.path.startswith(fa.BATCHES)]


@pytest.fixture
def batch_out(app, world, ai, monkeypatch):
    """The world's mail out in a batch to Claude, which has ended there."""
    monkeypatch.setattr(assistant.calls, "MODEL", None)
    monkeypatch.setattr(assistant.assessments, "BATCH_POLL", 0)
    sent = assistant.assessments.batch_submit(at_least=1, keep=0)
    assert sent > 0 and assistant.governor().snapshot()["reserved_micros"] > 0
    ai.end_batch()
    ai.requests.clear()
    return sent


def test_a_batch_sent_to_claude_is_let_go_once_another_provider_is_chosen_and_its_mail_goes_to_that_one(
        app, batch_out, ai, openai_api, fake_keychain):
    assert app.post("/api/preferences", {"ai_provider": "openai", "ai_base_url": openai_api.url}).status == 200
    assert app.post("/api/ai/key", {"provider": "openai", "key": fo.KEY}).status == 200
    assistant.assessments.round()
    assert ai.requests == []                                       # nothing brought in, nothing removed or cancelled there
    assert len(openai_api.chats) == batch_out                      # every message it held, summarized by the one chosen
    assert assistant.governor().snapshot()["reserved_micros"] == 0
    assert "batch_waiting" not in app.get("/api/digest").json["ai"]
    assert app.post("/api/preferences", {"ai_provider": "anthropic"}).status == 200
    assert assistant.assessments.batch_collect() == 0 and ai.requests == []


def test_a_batch_sent_to_claude_is_let_go_with_another_provider_chosen_and_ai_off(app, batch_out, ai, openai_api):
    assert app.post("/api/preferences", {"ai_provider": "openai", "ai_base_url": openai_api.url,
                                         "ai_enabled": False}).status == 200
    assert assistant.assessments.round() is None
    assert (ai.requests, openai_api.requests) == ([], [])
    assert assistant.assessments.batch_status() == {"waiting": 0, "state": None, "estimate_micros": 0}


def test_a_batch_sent_to_claude_waits_while_ai_is_off_and_comes_in_once_it_is_on(app, batch_out, ai):
    assert app.post("/api/preferences", {"ai_enabled": False}).status == 200
    assert assistant.assessments.batch_collect() == 0
    assert ai.requests == [] and assistant.assessments.batch_status()["waiting"] == batch_out
    assert app.post("/api/preferences", {"ai_enabled": True}).status == 200
    assert assistant.assessments.batch_collect() == batch_out
