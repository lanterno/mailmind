"""The AI providers (#49): what each is, which is in force and why AI is off, in the order a person can do something about it,
and the models a provider's list offers a person to choose."""
import pytest

from mailmind.assistant.domain.providers import PROVIDERS, chat_models, in_force, on_loopback
from mailmind.kernel.ai_provider import AI_PROVIDERS

KEY = "sk-test-not-a-real-key"


def engine(provider="anthropic", *, enabled=True, model="", base_url="", key=KEY, key_from="mailmind.config.json"):
    return in_force(enabled=enabled, provider=provider, model=model, base_url=base_url, key=key, key_from=key_from)


def test_each_provider_is_reached_its_way_at_its_own_url_and_asks_its_own_model():
    assert list(PROVIDERS) == list(AI_PROVIDERS)
    assert {p.id: (p.label, p.wire, p.base_url, p.key, p.model) for p in PROVIDERS.values()} == {
        "anthropic": ("Claude (Anthropic)", "anthropic", "https://api.anthropic.com", "needed", "claude-opus-5-5"),
        "openai": ("OpenAI", "openai", "https://api.openai.com/v1", "needed", "gpt-6.1-sol"),
        "gemini": ("Google Gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai", "needed",
                   "gemini-4-hydrogen"),
        "local": ("A model on this Mac", "openai", "http://127.0.0.1:11434/v1", "optional", ""),
        "compatible": ("Another OpenAI-compatible service", "openai", "", "optional", ""),
    }


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_a_provider_with_a_key_is_on_with_its_own_model_and_url_unless_another_is_chosen(provider):
    e = engine(provider)
    assert (e.off, e.model, e.base_url, e.key, e.key_from) == (None, PROVIDERS[provider].model, PROVIDERS[provider].base_url,
                                                               KEY, "mailmind.config.json")
    chosen = engine(provider, model="my-model", base_url="https://proxy.example.test/v1")
    assert (chosen.off, chosen.model, chosen.base_url) == (None, "my-model", "https://proxy.example.test/v1")


def test_turned_off_wins_over_every_other_reason():
    assert engine("compatible", enabled=False, key="").off == "turned-off"
    assert engine("anthropic", enabled=False).off == "turned-off"


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_a_provider_that_needs_a_key_is_off_without_one(provider):
    assert engine(provider, key="").off == "no-key"


def test_a_model_on_this_mac_needs_no_key_but_needs_a_model():
    e = engine("local", key="", key_from="")
    assert (e.off, e.key, e.base_url) == ("no-model", "", "http://127.0.0.1:11434/v1")
    e = engine("local", model="llama3.3", key="")
    assert (e.off, e.model, e.local) == (None, "llama3.3", True)
    assert engine("local", model="llama3.3").key == KEY


def test_a_compatible_service_needs_its_url_then_a_model_and_a_key_only_if_it_has_one():
    assert engine("compatible", key="").off == "no-url"
    assert engine("compatible", key="", base_url="https://llm.example.test/v1").off == "no-model"
    e = engine("compatible", key="", base_url="https://llm.example.test/v1", model="mixtral-9x")
    assert (e.off, e.key, e.local) == (None, "", False)
    assert engine("compatible", base_url="https://llm.example.test/v1", model="mixtral-9x").key == KEY


def test_a_service_on_this_mac_is_local_and_one_elsewhere_is_not():
    assert engine("compatible", base_url="http://localhost:8080/v1", model="m").local
    assert engine("anthropic", base_url="http://127.0.0.1:9/").local
    assert not engine("openai").local and not engine("anthropic").local
    assert on_loopback("http://[::1]:11434/v1") and not on_loopback("https://llm.example.test") and not on_loopback("http://[")


def test_a_call_on_this_mac_waits_five_minutes_as_its_model_may_be_loading_and_one_elsewhere_two():
    assert engine("local", model="llama3.3").timeout == 300
    assert engine("compatible", base_url="http://localhost:8080/v1", model="m").timeout == 300
    assert engine("anthropic", base_url="http://127.0.0.1:9/").timeout == 300
    assert engine("compatible", base_url="https://llm.example.test/v1", model="m").timeout == 120
    assert engine("openai").timeout == engine("anthropic").timeout == 120


def test_the_key_stays_out_of_an_engines_repr():
    assert KEY not in repr(engine("openai"))


def test_a_list_offers_only_what_can_chat_sorted_once_each_without_gemini_s_prefix():
    ids = ["gpt-6.1-sol", "text-embedding-4-large", "gpt-6-tts", "whisper-2", "dall-e-4", "gpt-image-2", "gpt-6-audio",
           "gpt-6-realtime", "gpt-6-transcribe", "omni-moderation-2", "gpt-6-search-preview", "gemini-live-2", "gpt-6-luna",
           "o3", "gpt-6-luna"]
    assert chat_models("openai", ids) == ["gpt-6-luna", "gpt-6.1-sol", "o3"]
    assert chat_models("gemini", ["models/gemini-4-hydrogen", "models/text-embedding-005", "models/gemini-4-carbon",
                                  "gemini-4-carbon", "models/"]) == ["gemini-4-carbon", "gemini-4-hydrogen"]


@pytest.mark.parametrize("provider", ["local", "compatible"])
def test_a_list_from_a_model_on_this_mac_or_a_compatible_service_drops_what_cant_chat_too(provider):
    ids = ["nomic-embed-text", "qwen3:8b", "llama3.3:latest", "whisper-large-v3", "", 7, None]
    assert chat_models(provider, ids) == ["llama3.3:latest", "qwen3:8b"]


def test_claudes_list_is_offered_whole():
    assert chat_models("anthropic", ["claude-embed-7", "claude-opus-9", "", 7, None]) == ["claude-embed-7", "claude-opus-9"]
