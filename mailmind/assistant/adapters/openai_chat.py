"""OpenAI's Chat Completions over HTTP, on the standard library: the language model for every provider but Claude (#49), OpenAI,
Gemini through Google's OpenAI-compatible endpoint, Ollama on this Mac and any other compatible service, each given its base
URL, its key (or none) and its model. What goes on the wire and how a reply reads is `chat_wire`'s.

It asks `POST {base}/chat/completions`, with `Authorization: Bearer <key>` when there is a key, and no proxy: the request
goes to the host the URL names. It tries the statuses a retry can help (408, 409, 429 and 5xx), and a connection that
failed, twice more, honouring retry-after, as the Anthropic SDK does. Ollama and a compatible service may not take
structured output: a 400 that names `response_format` is asked once more without it, and the reply read as free-form JSON.
A rejected key's error holds none of what the service said, which can quote part of the key back. It takes no batch.
"""
import http.client
import json
import random
import ssl
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from mailmind.assistant.adapters import chat_wire
from mailmind.assistant.application.ports import BatchResult, ModelError, NoBatches, Prompt, Reply
from mailmind.kernel.ai_provider import AiProvider
from mailmind.platform.db import txguard

TIMEOUT = 120                      # seconds per attempt
RETRIES = 2                        # after the first attempt
LONGEST_WAIT = 60                  # seconds: a retry-after past this isn't waited for
_RETRIED = frozenset({408, 409, 429})
_AUTH = frozenset({401, 403})
_FALLS_BACK = frozenset({"ollama", "compatible"})     # may not take response_format
_NO_BATCHES = "Batches need Claude as the AI provider."


class _Answer:
    def __init__(self, status: int, body: bytes, headers: Mapping[str, str]):
        self.status, self.body, self.headers = status, body, headers

    def json(self) -> Any:
        try:
            return json.loads(self.body or b"null")
        except ValueError:
            return None


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


def _said(answer: _Answer) -> str:
    """What a service said of an error: OpenAI's {"error": {"message"}}, or Ollama's {"error": "..."}."""
    body = answer.json()
    err = body.get("error") if isinstance(body, dict) else None
    said = err.get("message") if isinstance(err, dict) else err
    return str(said or "")[:200]


class Http:
    """One service's HTTP: requests to `base_url` with `key`, each attempt its own connection."""

    def __init__(self, provider: AiProvider, base_url: str, key: str, timeout: float = TIMEOUT):
        self.provider: AiProvider = provider
        self._key, self._timeout = key, timeout
        self._base = urlsplit(base_url.rstrip("/"))

    def send(self, method: str, path: str, body: dict[str, Any] | None = None) -> _Answer:
        """One attempt. Raises ModelError when nothing answered."""
        base = self._base
        headers = {"Accept": "application/json", "User-Agent": "MailMind"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn: http.client.HTTPConnection
        if base.scheme == "https":
            conn = http.client.HTTPSConnection(base.hostname or "", base.port, timeout=self._timeout,
                                               context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(base.hostname or "", base.port, timeout=self._timeout)
        try:
            conn.request(method, base.path + path, body=data, headers=headers)
            resp = conn.getresponse()
            return _Answer(resp.status, resp.read(), {k.lower(): v for k, v in resp.getheaders()})
        except TimeoutError:
            raise ModelError(f"{self.provider} unreachable: timed out") from None
        except ConnectionRefusedError:
            raise ModelError(f"{self.provider} unreachable: connection refused") from None
        except (OSError, http.client.HTTPException) as e:
            raise ModelError(f"{self.provider} unreachable: {type(e).__name__}") from None
        finally:
            conn.close()

    def refused(self, answer: _Answer) -> ModelError:
        """The ModelError for an answer that isn't a 2xx, with what the service said unless it refused the key."""
        if answer.status in _AUTH:
            return ModelError(f"{self.provider} API {answer.status}: the key was refused", answer.status)
        said = _said(answer)
        if self._key:
            said = said.replace(self._key, "<key>")
        return ModelError(f"{self.provider} API {answer.status}: {said}", answer.status)


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
            except ModelError:
                if attempt >= self._retries:
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
        answer = self._post(chat_wire.body(prompt, provider))
        if (answer.status == 400 and prompt.schema is not None and provider in _FALLS_BACK
                and b"response_format" in answer.body):
            answer = self._post(chat_wire.body(prompt, provider, with_format=False))
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
