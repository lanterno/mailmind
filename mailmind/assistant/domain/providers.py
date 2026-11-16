"""The AI providers MailMind can ask (#49), and which of them is in force.

Claude is the default. The others are reached through one adapter that speaks OpenAI's Chat Completions, given a base URL,
a key (or none) and a model. Each provider says how it is reached (`wire`), where by default, whether it takes a key, and the
model it asks unless a person picks one. `in_force` reads what a person chose into the `Engine`: the provider, the model and
URL it is asked at, its key, and why AI is off, in the order a person can do something about it.
"""
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from mailmind.assistant.domain import models
from mailmind.kernel import ai_provider
from mailmind.kernel.ai_provider import AiProvider

type Wire = Literal["anthropic", "openai"]
type KeyNeed = Literal["needed", "optional"]
type AiOff = Literal["turned-off", "no-key", "no-model", "no-url"]

# Why AI is off, in words a person reads: an answer, the CLI and the startup line say them. None holds a secret.
OFF_WORDS: dict[AiOff, str] = {"turned-off": "AI is off: it is turned off in Settings",
                               "no-key": "AI is off: there is no API key",
                               "no-url": "AI is off: the service has no URL",
                               "no-model": "AI is off: no model is chosen"}

CONFIG = "mailmind.config.json"           # where a key came from, for every provider but Claude's from the environment
TIMEOUT = 120                             # seconds a call's attempt waits
LOCAL_TIMEOUT = 300                       # ...on this Mac, where a model loads on its first use

# The model servers a person may run on this Mac, each at its OpenAI-compatible API on its own port, in the order Settings
# offers the ones that answer.
LOCAL_SERVERS: tuple[tuple[str, str], ...] = (("Ollama", "http://127.0.0.1:11434/v1"),
                                              ("LM Studio", "http://127.0.0.1:1234/v1"),
                                              ("llama.cpp", "http://127.0.0.1:8080/v1"),
                                              ("vLLM", "http://127.0.0.1:8000/v1"),
                                              ("Jan", "http://127.0.0.1:1337/v1"),
                                              ("GPT4All", "http://127.0.0.1:4891/v1"))


@dataclass(frozen=True)
class Provider:
    id: AiProvider
    label: str              # what a person is shown
    wire: Wire
    base_url: str           # "" where the person gives it
    key: KeyNeed
    model: str              # "" where the person picks one


PROVIDERS: dict[AiProvider, Provider] = {p.id: p for p in (
    Provider("anthropic", "Claude (Anthropic)", "anthropic", "https://api.anthropic.com", "needed", models.DEFAULT),
    Provider("openai", "OpenAI", "openai", "https://api.openai.com/v1", "needed", "gpt-6.1-sol"),
    Provider("gemini", "Google Gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai", "needed",
             "gemini-4-hydrogen"),
    Provider("local", "A model on this Mac", "openai", "http://127.0.0.1:11434/v1", "optional", ""),     # Ollama's address
    Provider("compatible", "Another OpenAI-compatible service", "openai", "", "optional", ""),
)}

# What a model's id holds when it can't chat: every list but Claude's names these beside the ones that can (nomic-embed-text).
_NOT_CHAT = ("embed", "tts", "whisper", "dall-e", "image", "audio", "realtime", "transcribe", "moderation", "search", "live")


def on_loopback(url: str) -> bool:
    """Whether `url` names this Mac, as a URL a person may give a service names it: 127.0.0.1, ::1 or localhost in a URL
    with no user or password, query or fragment, and no port that is empty or outside 1 to 65535. Any other is elsewhere,
    so a model it serves that the price table doesn't know is priced, and its calls wait no longer."""
    return ai_provider.on_this_machine(url)


def sendable(key: str) -> bool:
    """Whether `key` can go in a request's header: printable ASCII with no space, as every provider's keys are. A key with a
    blank, a line break or another control character in it was pasted or read wrong, and would break the header it went in."""
    return all("!" <= ch <= "~" for ch in key)


@dataclass(frozen=True)
class Engine:
    """The AI in force: the provider chosen, the model asked unless a call names another, the base URL it is reached at,
    its key ("" for none) and where it was looked for (CONFIG, or ANTHROPIC_API_KEY's name), and why AI is off (None while
    it is on)."""
    provider: AiProvider
    model: str
    base_url: str
    key: str = field(repr=False)
    key_from: str
    off: AiOff | None

    @property
    def label(self) -> str:
        return PROVIDERS[self.provider].label

    @property
    def local(self) -> bool:
        """It runs on this Mac (a model on this Mac, or any service on loopback), so a model the price table doesn't know
        costs nothing."""
        return self.provider == "local" or on_loopback(self.base_url)

    @property
    def timeout(self) -> int:
        """Seconds an attempt waits: longer on this Mac, where a model is loaded on its first use."""
        return LOCAL_TIMEOUT if self.local else TIMEOUT


def in_force(*, enabled: bool, provider: AiProvider, model: str, base_url: str, key: str, key_from: str) -> Engine:
    """The Engine for what was chosen. `model` and `base_url` are "" for the provider's own, and `key` "" for none. Off, in
    order: turned off; a provider that needs a key with none; a service with no URL; one with no model."""
    p = PROVIDERS[provider]
    model, base_url = model or p.model, base_url or p.base_url
    off: AiOff | None = ("turned-off" if not enabled else
                         "no-key" if p.key == "needed" and not key else
                         "no-url" if not base_url else
                         "no-model" if not model else None)
    return Engine(provider, model, base_url, key, key_from, off)


def chat_models(provider: AiProvider, ids: Iterable[object]) -> list[str]:
    """The models a provider's list names, as a person picks from them: sorted, once each, without Gemini's `models/`, and
    for every provider but Claude, whose list holds no other kind, only the ones that can chat."""
    names = {i.removeprefix("models/") for i in ids if isinstance(i, str) and i.removeprefix("models/")}
    if provider != "anthropic":
        names = {n for n in names if not any(word in n for word in _NOT_CHAT)}
    return sorted(names)
