"""OpenAI's Chat Completions over HTTP, on the standard library: the language model for every provider but Claude (#49), OpenAI,
Gemini through Google's OpenAI-compatible endpoint, a model on this Mac and any other compatible service, each given its base
URL, its key (or none) and its model. What goes on the wire and how a reply reads is `chat_wire`'s.

It asks `POST {base}/chat/completions`, with `Authorization: Bearer <key>` when there is a key, each attempt an `exchange`:
no proxy, the request going to the host the URL names, its time limit a deadline for all of it, and an answer of 32 MiB at
most. It tries the statuses a retry can help (408, 409, 429 and 5xx), and a connection that failed, twice more, honouring
retry-after, as the Anthropic SDK does. A model on this Mac and a compatible service may not take structured output: a 400
that names `response_format` is asked once more without it, the schema stated in the prompt instead, and the reply read as
free-form JSON. OpenAI and Gemini are given a ceiling on a reply's tokens that a model may be unable to meet: a 400 whose
error names `max_tokens` or `max_completion_tokens` is asked once more without it. A rejected key's error holds none of what the
service said, which can quote part of the key back, and a key with a blank or a control character in it is never sent: it
is refused as a rejected key is. It takes no batch.
"""
import json
import random
import time
from collections.abc import Mapping
from typing import Any, cast

from mailmind.assistant.adapters import chat_wire, exchange
from mailmind.assistant.adapters.exchange import Answer as _Answer
from mailmind.assistant.application.ports import BatchResult, ModelError, NoBatches, Prompt, Reply
from mailmind.assistant.domain.providers import TIMEOUT, sendable
from mailmind.kernel.ai_provider import AiProvider
from mailmind.platform.db import txguard

RETRIES = 2                        # after the first attempt
LONGEST_WAIT = 60                  # seconds: a retry-after past this isn't waited for
SAID = 200                         # characters of what a service said an error kept
_RETRIED = frozenset({408, 409, 429})
_AUTH = frozenset({401, 403})
_KEY_INVALID = "API_KEY_INVALID"   # the reason Gemini's 400 gives for a key that isn't one
_NOT_VALID = "API key not valid"   # ...and how its message starts
_FALLS_BACK = frozenset({"local", "compatible"})      # may not take response_format
_CEILING_WORDS = ("max_tokens", "max_completion_tokens")         # in what a 400 says of a ceiling a model can't meet
_NO_BATCHES = "Batches need Claude as the AI provider."


def _retried(status: int) -> bool:
    return status in _RETRIED or status >= 500


def _wait(headers: Mapping[str, str] | None, attempt: int) -> float:
    """How long to wait before trying again: what retry-after-ms or retry-after asks, up to LONGEST_WAIT, else a backoff."""
    for name, scale in (("retry-after-ms", 1000), ("retry-after", 1)):
        try:
            asked = float((headers or {}).get(name) or "") / scale
        except ValueError:
            continue
        if 0 <= asked <= LONGEST_WAIT:
            return asked
    return min(0.5 * 2 ** attempt, 8) * random.uniform(0.75, 1)


def _error(answer: _Answer) -> Any:
    """The error a service answered with: OpenAI's {"error": {...}}, Ollama's {"error": "..."}, or the first of Gemini's
    [{"error": {...}}]."""
    body = answer.json()
    if isinstance(body, list) and body:
        body = cast(list[Any], body)[0]
    return cast(dict[str, Any], body).get("error") if isinstance(body, dict) else None


def _said(answer: _Answer) -> str:
    """What a service said of an error, all of it: an error object's message, or else the error itself; words as they are,
    and anything else as compact JSON."""
    err = _error(answer)
    said = cast(dict[str, Any], err).get("message") if isinstance(err, dict) else err
    if not said:
        return ""
    return said if isinstance(said, str) else json.dumps(said, separators=(",", ":"), ensure_ascii=False)


def _key_refused(answer: _Answer) -> bool:
    """Whether the service refused the key: a 401 or 403, or Gemini's 400 for a key that isn't one, whose error gives the
    reason API_KEY_INVALID or says "API key not valid"."""
    if answer.status in _AUTH:
        return True
    if answer.status != 400:
        return False
    err = _error(answer)
    details = cast(dict[str, Any], err).get("details") if isinstance(err, dict) else None
    if isinstance(details, list) and any(isinstance(d, dict) and cast(dict[str, Any], d).get("reason") == _KEY_INVALID
                                         for d in cast(list[Any], details)):
        return True
    return _said(answer).startswith(_NOT_VALID)


class Http:
    """One service's HTTP: requests to `base_url` with `key`, each attempt its own connection."""

    def __init__(self, provider: AiProvider, base_url: str, key: str, timeout: float = TIMEOUT):
        self.provider: AiProvider = provider
        self._key, self._timeout = key, timeout
        self._endpoint = exchange.endpoint(base_url)

    def send(self, method: str, path: str, body: dict[str, Any] | None = None) -> _Answer:
        """One attempt, which the timeout limits all told: connecting, asking and reading the whole answer, of exchange.MOST
        bytes at most. Raises ModelError when nothing answered, or nothing whole in time, and as a refused key, unsent, for a
        key that can't be one."""
        end = self._endpoint
        if end is None:
            raise ModelError(f"{self.provider} unreachable: no API address")
        if not sendable(self._key):
            raise self._refused_key(401)
        headers = {"Accept": "application/json", "User-Agent": "MailMind"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        try:
            return exchange.attempt(end, method, end.base + path, headers, data, self._timeout)
        except exchange.Unanswered as e:
            raise ModelError(f"{self.provider} unreachable: {e.why}") from None

    def _refused_key(self, status: int) -> ModelError:
        return ModelError(f"{self.provider} API {status}: the key was refused", status)

    def refused(self, answer: _Answer) -> ModelError:
        """The ModelError for an answer that isn't a 2xx, with what the service said unless it refused the key. A key
        refused with a 400 is refused as a 401 is, so everything that reads one reads it so."""
        if _key_refused(answer):
            return self._refused_key(answer.status if answer.status in _AUTH else 401)
        said = _said(answer)
        if self._key:
            said = said.replace(self._key, "<key>")         # before it is cut, which could leave part of the key
        return ModelError(f"{self.provider} API {answer.status}: {said[:SAID]}", answer.status)


class ChatModel:
    enabled = True
    takes_batches = False

    def __init__(self, provider: AiProvider, base_url: str, key: str, model: str, timeout: float = TIMEOUT,
                 retries: int = RETRIES, local: bool = False):
        self.model, self.local = model, local
        self._http = Http(provider, base_url, key, timeout)
        self._retries = retries

    def _post(self, body: dict[str, Any]) -> _Answer:
        """POST the chat completion, trying again what a retry can help. Raises ModelError when nothing answered."""
        attempt = 0
        while True:
            try:
                answer = self._http.send("POST", "/chat/completions", body)
            except ModelError as e:
                if e.permanent or attempt >= self._retries:
                    raise
                headers = None
            else:
                if not _retried(answer.status) or attempt >= self._retries:
                    return answer
                headers = answer.headers
            time.sleep(_wait(headers, attempt))
            attempt += 1

    def complete(self, prompt: Prompt) -> Reply:
        txguard.refuse_network("an AI provider request")
        provider = self._http.provider
        with_format, with_ceiling = True, True
        answer = self._post(chat_wire.body(prompt, provider))
        while answer.status == 400 and not _key_refused(answer):
            if (with_format and prompt.schema is not None and provider in _FALLS_BACK
                    and b"response_format" in answer.body):
                with_format = False
            elif with_ceiling and provider in chat_wire.CEILING and any(w in _said(answer) for w in _CEILING_WORDS):
                with_ceiling = False
            else:
                break
            answer = self._post(chat_wire.body(prompt, provider, with_format, with_ceiling))
        if not 200 <= answer.status < 300:
            raise self._http.refused(answer)
        resp = answer.json()
        if not isinstance(resp, dict):
            raise ModelError(f"{provider} answered what can't be read")
        return chat_wire.reply(resp, prompt.model)

    def submit_batch(self, requests: list[tuple[str, Prompt]]) -> str:
        raise NoBatches(_NO_BATCHES)

    def batch_ended(self, batch_id: str) -> bool:
        raise NoBatches(_NO_BATCHES)

    def batch_results(self, batch_id: str, asked: str) -> list[BatchResult]:
        raise NoBatches(_NO_BATCHES)

    def discard_batch(self, batch_id: str) -> None:
        raise NoBatches(_NO_BATCHES)


def list_models(provider: AiProvider, base_url: str, key: str, timeout: float = TIMEOUT) -> list[str]:
    """The ids GET {base}/models names, asked once: a person is waiting. Raises ModelError as a call does."""
    txguard.refuse_network("an AI provider's list of models")
    service = Http(provider, base_url, key, timeout)
    answer = service.send("GET", "/models")
    if not 200 <= answer.status < 300:
        raise service.refused(answer)
    return chat_wire.model_ids(answer.json())
