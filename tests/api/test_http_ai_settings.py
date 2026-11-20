"""AI's settings over HTTP (#49): the switch, the provider, its model and its URL in the preferences; the models a provider
lists for a person to choose from; what the digest's `ai` says is in force and why AI is off; and a call to a provider other
than Claude, against the fake OpenAI-compatible API on loopback. Each provider's key is kept as tests/api/test_http_keychain.py
keeps Claude's. Nothing here reaches a real provider: a test that lists a local model's gives it the fake's URL first.
"""
import json
import socket
from urllib.parse import urlencode

import pytest

import problems
from fakes import anthropic_api as fa
from fakes import openai_api as fo

MODEL_REFUSED = "The AI model’s name must be up to 200 letters, digits and . _ : / @ + - characters."
URL_REFUSED = ("The AI service’s URL must be https, or http on this Mac, in at most 500 characters, with no user, password, "
               "query or fragment.")


def prefs(app):
    got = app.get("/api/preferences").json["preferences"]
    return {k: v for k, v in got.items() if k.startswith("ai_") and k != "ai_daily_limit_usd"}


def choose(app, **asked):
    r = app.post("/api/preferences", asked)
    assert r.status == 200, r.json
    return r


def status(app):
    return app.get("/api/digest").json["ai"]


def models(app, provider):
    return app.get("/api/ai/models?" + urlencode({"provider": provider}))


def keep(app, provider, key=fo.KEY):
    assert app.post("/api/ai/key", {"provider": provider, "key": key}).status == 200


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── the preferences ───────────────────────────────────────────────────────────

def test_ai_mode_starts_off_with_claude_and_its_own_model_and_url(app):
    assert prefs(app) == {"ai_enabled": False, "ai_provider": "anthropic", "ai_model": "", "ai_base_url": ""}


def test_ai_mode_starts_on_once_a_key_is_kept_and_stays_as_it_is_set(app, fake_keychain):
    keep(app, "openai")
    assert prefs(app)["ai_enabled"] is True
    choose(app, ai_enabled=False)
    assert prefs(app)["ai_enabled"] is False
    keep(app, "gemini")
    assert prefs(app)["ai_enabled"] is False


def test_with_claudes_key_in_the_environment_ai_mode_starts_on(app, ai):
    assert prefs(app)["ai_enabled"] is True


def test_a_model_or_url_none_can_be_is_refused_in_words_and_a_provider_or_switch_as_a_bad_request(app):
    before = prefs(app)
    for asked, said in [({"ai_model": "has space"}, problems.refused(MODEL_REFUSED)),
                        ({"ai_model": "m" * 201}, problems.refused(MODEL_REFUSED)),
                        ({"ai_base_url": "http://llm.example.test/v1"}, problems.refused(URL_REFUSED)),
                        ({"ai_base_url": "https://llm.example.test/v1?key=x"}, problems.refused(URL_REFUSED)),
                        ({"ai_base_url": "https://" + "a" * 500}, problems.refused(URL_REFUSED)),
                        ({"ai_base_url": "https://llm.example.test:/v1"}, problems.refused(URL_REFUSED)),
                        ({"ai_base_url": "http://127.0.0.1:/v1"}, problems.refused(URL_REFUSED)),
                        ({"ai_base_url": "http://[::1]:/v1"}, problems.refused(URL_REFUSED)),
                        ({"ai_base_url": "https://llm.example.test:0/v1"}, problems.refused(URL_REFUSED)),
                        ({"ai_base_url": "https://llm.example.test:65536/v1"}, problems.refused(URL_REFUSED)),
                        ({"ai_base_url": "https://[v1.ab]/v1"}, problems.refused(URL_REFUSED)),
                        ({"ai_base_url": "https://[fe80::1%25en0]/v1"}, problems.refused(URL_REFUSED)),
                        ({"ai_provider": "mistral"}, problems.BAD_REQUEST), ({"ai_enabled": "yes"}, problems.BAD_REQUEST)]:
        r = app.post("/api/preferences", asked)
        assert (r.status, r.json) == (400, said), asked
    assert prefs(app) == before


def test_a_model_on_this_mac_at_a_url_elsewhere_is_refused_in_words(app):
    before = prefs(app)
    r = app.post("/api/preferences", {"ai_provider": "local", "ai_base_url": "https://gpu.example.test/v1"})
    assert (r.status, r.json) == (400, problems.refused("A model on this Mac must be at 127.0.0.1, ::1 or localhost."))
    assert prefs(app) == before
    choose(app, ai_provider="local", ai_base_url="http://localhost:1234/v1")
    assert prefs(app)["ai_base_url"] == "http://localhost:1234/v1"


def test_a_url_is_kept_without_its_trailing_slash_and_another_provider_sets_model_and_url_back(app):
    choose(app, ai_provider="compatible", ai_model="mixtral-9x", ai_base_url="https://llm.example.test/v1/")
    assert prefs(app) == {"ai_enabled": False, "ai_provider": "compatible", "ai_model": "mixtral-9x",
                          "ai_base_url": "https://llm.example.test/v1"}
    choose(app, ai_provider="local", ai_model="llama3.3")
    assert prefs(app) == {"ai_enabled": False, "ai_provider": "local", "ai_model": "llama3.3", "ai_base_url": ""}
    choose(app, ai_enabled=True)
    assert prefs(app)["ai_model"] == "llama3.3"
    choose(app, ai_provider="anthropic")
    assert prefs(app) == {"ai_enabled": True, "ai_provider": "anthropic", "ai_model": "", "ai_base_url": ""}


# ── what is in force ──────────────────────────────────────────────────────────

def test_the_digest_says_which_provider_and_why_ai_is_off(app, fake_keychain):
    st = status(app)
    assert (st["engine"], st["provider"], st["off"], st["model"]) == ("none", "anthropic", "turned-off", "")
    for asked, said in [({"ai_enabled": True}, ("anthropic", "no-key")), ({"ai_provider": "openai"}, ("openai", "no-key")),
                        ({"ai_provider": "compatible"}, ("compatible", "no-url")),
                        ({"ai_provider": "compatible", "ai_base_url": "https://llm.example.test/v1"}, ("compatible", "no-model")),
                        ({"ai_provider": "local"}, ("local", "no-model")),
                        ({"ai_provider": "local", "ai_model": "llama3.3", "ai_enabled": False}, ("local", "turned-off"))]:
        choose(app, **asked)
        st = status(app)
        assert (st["provider"], st["off"], st["engine"], st["model"]) == (*said, "none", ""), asked
        assert "llm.example.test" not in str(st)


def test_the_status_says_when_the_model_chosen_on_this_mac_runs_in_a_cloud(app):
    """Ollama serves its cloud models at the same address as the rest: one named `-cloud` or `:cloud` sends mail off this
    Mac, and the status says so before AI is turned on."""
    assert status(app)["cloud_model"] is False
    choose(app, ai_provider="local", ai_model="qwen3:8b")
    assert status(app)["cloud_model"] is False
    choose(app, ai_model="gpt-oss:120b-cloud")
    assert status(app)["cloud_model"] is True
    choose(app, ai_provider="compatible", ai_base_url="http://localhost:1234/v1", ai_model="glm-4.6:cloud")
    assert status(app)["cloud_model"] is True
    choose(app, ai_base_url="https://llm.example.test/v1")
    assert status(app)["cloud_model"] is False


def test_with_claudes_key_it_is_on_until_turned_off_and_then_a_route_sends_nothing(app, world, ai):
    st = status(app)
    assert (st["engine"], st["provider"], st["off"], st["model"]) == ("api", "anthropic", None, "claude-opus-5-5")
    choose(app, ai_enabled=False)
    assert (status(app)["engine"], status(app)["off"]) == ("none", "turned-off")
    r = app.post("/api/ai/rewrite", {"op": "shorten", "text": "see you thursday"})
    assert (r.status, r.json) == (409, problems.ai_off("turned-off"))
    app.work("assess")
    assert ai.requests == []
    choose(app, ai_enabled=True)
    assert app.post("/api/ai/rewrite", {"op": "shorten", "text": "see you thursday"}).status == 200
    assert len(ai.requests) == 1


def test_another_provider_is_asked_in_claudes_place(app, world, ai, openai_api, fake_keychain):
    choose(app, ai_provider="openai", ai_base_url=openai_api.url)
    keep(app, "openai")
    st = status(app)
    assert (st["engine"], st["provider"], st["off"], st["model"], st["key_from"]) == (
        "api", "openai", None, "gpt-6.1-sol", "mailmind.config.json")
    r = app.post("/api/ai/rewrite", {"op": "shorten", "text": "see you thursday"})
    assert r.status == 200 and r.json["text"] == "stub text"
    [req] = openai_api.chats
    assert (req.key, req.body["model"], "see you thursday" in req.prompt) == (fo.KEY, "gpt-6.1-sol", True)
    assert ai.requests == []
    assert status(app)["last_ok"] is True


NO_CREDIT = [
    ("openai", fo.no_quota(), "OpenAI"),
    ("gemini", fo.gemini_error(429, "You exceeded your current quota, please check your plan and billing details.",
                               kind="RESOURCE_EXHAUSTED", quota="GenerateRequestsPerDayPerProjectPerModel-FreeTier"),
     "Gemini"),
]


@pytest.mark.parametrize("provider, refusal, account", NO_CREDIT, ids=[p for p, _, _ in NO_CREDIT])
def test_a_provider_that_refuses_for_want_of_credit_is_asked_once_and_the_status_names_the_account(
        app, world, openai_api, fake_keychain, provider, refusal, account):
    choose(app, ai_enabled=True, ai_provider=provider, ai_base_url=openai_api.url)
    keep(app, provider)
    openai_api.queue(refusal, refusal, refusal)
    r = app.post("/api/ai/rewrite", {"op": "shorten", "text": "see you thursday"})
    assert r.status == 502 and len(openai_api.chats) == 1
    assert (status(app)["last_ok"], status(app)["last_error"]) == (False, f"the {account} account has no credit left")


def test_the_bridges_password_is_never_an_ai_key(app, world, openai_api, fake_keychain):
    """A hand edit may name it for a provider's key: it is no key, never sent, wherever it would go."""
    bridge = "bridge-password-not-a-key"
    assert app.post("/api/accounts/bridge-password", {"password": bridge}).status == 200
    with app.stopped():
        written = json.loads((world.dir / "mailmind.config.json").read_text())
        written.update(openai_api_key={"proton_bridge": True}, compatible_api_key={"proton_bridge": True})
        (world.dir / "mailmind.config.json").write_text(json.dumps(written))
    choose(app, ai_enabled=True, ai_provider="openai", ai_base_url=openai_api.url)
    assert (status(app)["engine"], status(app)["off"]) == ("none", "no-key")
    assert (models(app, "openai").status, models(app, "openai").json) == (
        400, problems.refused("Keep a key for this provider first."))
    openai_api.key = None
    choose(app, ai_provider="compatible", ai_base_url=openai_api.url, ai_model="mixtral-9x")
    assert app.post("/api/ai/rewrite", {"op": "shorten", "text": "see you thursday"}).status == 200
    assert models(app, "compatible").status == 200
    assert [r.path for r in openai_api.requests] == ["/chat/completions", "/models"]
    assert not [r for r in openai_api.requests if "authorization" in r.headers or bridge in str(r.body)]


# ── the models a provider lists ───────────────────────────────────────────────

def test_claudes_models_are_listed_with_its_own_as_the_default(app, ai):
    ai.models = ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"]
    r = models(app, "anthropic")
    assert (r.status, r.json) == (200, {"ok": True, "models": ["claude-haiku-5-5", "claude-opus-5-5", "claude-sonnet-5-5"],
                                        "default": "claude-opus-5-5"})
    [req] = ai.requests
    assert (req.method, req.path.split("?")[0], req.key) == ("GET", "/v1/models", fa.KEY)


def test_openais_list_offers_only_the_models_that_chat(app, openai_api, fake_keychain):
    choose(app, ai_provider="openai", ai_base_url=openai_api.url)
    keep(app, "openai")
    r = models(app, "openai")
    assert (r.status, r.json) == (200, {"ok": True, "models": ["gpt-6-luna", "gpt-6.1-sol"], "default": "gpt-6.1-sol"})
    [req] = openai_api.requests
    assert (req.method, req.path, req.key) == ("GET", "/models", fo.KEY)


def test_geminis_list_drops_its_models_prefix(app, openai_api, fake_keychain):
    openai_api.models = ["models/gemini-4-hydrogen", "models/text-embedding-005", "models/gemini-4-carbon"]
    choose(app, ai_provider="gemini", ai_base_url=openai_api.url)
    keep(app, "gemini")
    assert models(app, "gemini").json == {"ok": True, "models": ["gemini-4-carbon", "gemini-4-hydrogen"],
                                          "default": "gemini-4-hydrogen"}


def test_a_model_on_this_mac_lists_its_models_with_no_key_and_no_default_even_with_ai_turned_off(app, openai_api):
    openai_api.key = None
    openai_api.models = ["qwen3:8b", "llama3.3:latest", "nomic-embed-text"]
    choose(app, ai_provider="local", ai_base_url=openai_api.url, ai_enabled=False)
    r = models(app, "local")
    assert (r.status, r.json) == (200, {"ok": True, "models": ["llama3.3:latest", "qwen3:8b"], "default": None})
    assert "authorization" not in openai_api.requests[0].headers


def test_a_compatible_service_lists_at_its_url_with_a_key_only_if_one_is_kept(app, openai_api, fake_keychain):
    r = models(app, "compatible")
    assert (r.status, r.json) == (400, problems.refused("Give the service's URL first."))
    openai_api.key = None
    choose(app, ai_provider="compatible", ai_base_url=openai_api.url)
    assert models(app, "compatible").json["default"] is None
    assert "authorization" not in openai_api.requests[-1].headers
    keep(app, "compatible")
    assert models(app, "compatible").status == 200
    assert openai_api.requests[-1].key == fo.KEY


def test_another_providers_models_are_listed_at_its_own_url_never_at_the_one_chosen_for_the_provider_in_force(
        app, ai, openai_api, fake_keychain):
    """The URL a person chose belongs to the provider in force: listing another's at it would send that one's key there."""
    choose(app, ai_provider="compatible", ai_base_url=openai_api.url, ai_model="mixtral-9x")
    keep(app, "compatible")
    ai.models = ["claude-opus-5-5"]
    assert models(app, "anthropic").json["models"] == ["claude-opus-5-5"]             # at Claude's own
    [req] = ai.requests
    assert (req.method, req.path.split("?")[0], req.key) == ("GET", "/v1/models", fa.KEY)
    assert openai_api.requests == []

    choose(app, ai_provider="openai", ai_base_url=openai_api.url)
    keep(app, "openai")
    r = models(app, "compatible")                                                     # it has none of its own
    assert (r.status, r.json) == (400, problems.refused("Give the service's URL first."))
    assert openai_api.requests == []
    assert models(app, "openai").status == 200                                        # the one in force, at its own
    assert [(r.path, r.key) for r in openai_api.requests] == [("/models", fo.KEY)]
    assert len(ai.requests) == 1


# ── the model servers on this Mac ─────────────────────────────────────────────

def test_the_model_servers_on_this_mac_that_answer_are_listed_in_order_with_ai_mode_off(app, local_ai):
    local_ai["Ollama"].models = ["qwen3:8b", "nomic-embed-text", "llama3.3:latest"]
    local_ai["LM Studio"].models = ["mistral-small-4"]
    assert prefs(app)["ai_enabled"] is False
    r = app.get("/api/ai/local")
    assert (r.status, r.json) == (200, {"ok": True, "servers": [
        {"name": "Ollama", "url": local_ai["Ollama"].url, "models": ["llama3.3:latest", "qwen3:8b"]},
        {"name": "LM Studio", "url": local_ai["LM Studio"].url, "models": ["mistral-small-4"]}]})


def test_a_model_server_on_this_mac_is_never_sent_a_key_even_one_kept_for_a_model_on_this_mac(app, local_ai, fake_keychain):
    keep(app, "local")
    assert [s["name"] for s in app.get("/api/ai/local").json["servers"]] == ["Ollama", "LM Studio"]
    for fake in local_ai.values():
        [req] = fake.requests
        assert (req.method, req.path, req.key) == ("GET", "/models", "")


def test_one_that_answers_no_list_of_models_is_left_out(app, local_ai):
    local_ai["Ollama"].queue((200, {"models": [{"name": "llama3.3"}]}))
    assert [s["name"] for s in app.get("/api/ai/local").json["servers"]] == ["LM Studio"]


def test_with_no_model_server_on_this_mac_there_are_none(app):
    r = app.get("/api/ai/local")
    assert (r.status, r.json) == (200, {"ok": True, "servers": []})


def test_a_provider_with_no_key_kept_is_refused_before_anything_is_sent(app):
    for provider in ("openai", "gemini"):
        r = models(app, provider)
        assert (r.status, r.json) == (400, problems.refused("Keep a key for this provider first.")), provider


def test_a_key_the_provider_refuses_is_said_in_words_without_the_key(app, openai_api, fake_keychain):
    wrong = "sk-test-wrong-key-abcdefghijkl"
    choose(app, ai_provider="openai", ai_base_url=openai_api.url)
    keep(app, "openai", wrong)
    r = models(app, "openai")
    assert (r.status, r.json) == (400, problems.refused("The provider refused the key."))


def test_a_provider_that_isnt_there_is_unreachable(app):
    choose(app, ai_provider="local", ai_base_url=f"http://127.0.0.1:{free_port()}/v1")
    r = models(app, "local")
    assert (r.status, r.json) == (502, problems.unreachable("Couldn't reach the provider."))


@pytest.mark.parametrize("query", ["", "?provider=mistral", "?provider=", "?provider=Anthropic"])
def test_a_provider_none_can_be_is_a_bad_request(app, query):
    r = app.get("/api/ai/models" + query)
    assert (r.status, r.json) == (400, problems.BAD_REQUEST)
