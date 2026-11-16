"""The Messages API through the official Anthropic SDK: Claude, the default language model the server and every command talk to.

The client is given its key and base URL, so the SDK reads no credential or profile of its own
from the environment or the disk. It retries the statuses in ports.RETRIED, twice, honouring
retry-after, and raises typed errors, which become ModelError here. The server-side refusal
fallback is a beta, so a prompt that asks for it goes through client.beta.messages. A key with a
blank or a control character in it is never sent: it is refused as a rejected key is.

Only this module imports anthropic.
"""
from collections.abc import Callable

import anthropic

from mailmind.assistant.adapters import wire
from mailmind.assistant.application.ports import BatchResult, ModelError, Prompt, Reply
from mailmind.assistant.domain import models
from mailmind.assistant.domain.models import FALLBACK_BETA
from mailmind.assistant.domain.providers import TIMEOUT, sendable
from mailmind.platform.db import txguard

RETRIES = 2                        # after the first attempt
DEFAULT_URL = "https://api.anthropic.com"
MODELS_PAGE = 1000                 # the most the API lists at once
KEY_REFUSED = "Claude API 401: the key was refused"     # for a key that can't be one, never sent


def _error_text(e: anthropic.APIStatusError) -> str:
    body = e.body if isinstance(e.body, dict) else {}
    inner = body.get("error")
    err = inner if isinstance(inner, dict) else body
    return str(err.get("message") or e.message or "")[:200]


def _calling[T](call: Callable[[], T], key_ok: bool) -> T:
    """Make a call on this thread, as the SDK's retries are, its errors as ModelError: none with a key that isn't `key_ok`."""
    txguard.refuse_network("a Claude API request")
    if not key_ok:
        raise ModelError(KEY_REFUSED, 401)
    try:
        return call()
    except anthropic.APIStatusError as e:
        raise ModelError(f"Claude API {e.status_code}: {_error_text(e)}", e.status_code) from None
    except anthropic.APIConnectionError as e:
        raise ModelError(f"Claude API unreachable: {e.message}") from None
    except anthropic.AnthropicError as e:
        raise ModelError(f"Claude API failed: {type(e).__name__}") from None


def _client(key: str, base_url: str | None, timeout: float, retries: int) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=key, base_url=base_url or DEFAULT_URL, timeout=timeout, max_retries=retries)


class AnthropicModel:
    enabled = True
    takes_batches = True

    def __init__(self, key: str, base_url: str | None = None, timeout: float = TIMEOUT,
                 retries: int = RETRIES, model: str = models.DEFAULT, local: bool = False):
        self.model, self.local = model, local
        self._client = _client(key, base_url, timeout, retries)
        self._key_ok = sendable(key)

    def complete(self, prompt: Prompt) -> Reply:
        body = wire.body(prompt)
        if prompt.fallback:
            resp = _calling(lambda: self._client.beta.messages.create(**body, fallbacks="default",
                                                                      betas=[FALLBACK_BETA]), self._key_ok)
        else:
            resp = _calling(lambda: self._client.messages.create(**body), self._key_ok)
        return wire.reply(resp.to_dict(), prompt.model)

    def submit_batch(self, requests: list[tuple[str, Prompt]]) -> str:
        batches = self._client.with_options(max_retries=0).messages.batches
        return _calling(lambda: batches.create(
            requests=[wire.batch_request(i, p) for i, p in requests]), self._key_ok).id  # pyright: ignore[reportArgumentType]

    def batch_ended(self, batch_id: str) -> bool:
        batch = _calling(lambda: self._client.messages.batches.retrieve(batch_id), self._key_ok)
        return batch.processing_status == "ended"

    def batch_results(self, batch_id: str, asked: str) -> list[BatchResult]:
        return _calling(lambda: [wire.batch_result(line.to_dict(), asked)
                                 for line in self._client.messages.batches.results(batch_id)], self._key_ok)

    def discard_batch(self, batch_id: str) -> None:
        _calling(lambda: self._client.messages.batches.delete(batch_id), self._key_ok)


def list_models(key: str, base_url: str | None = None, timeout: float = TIMEOUT) -> list[str]:
    """The ids of the models the key may ask, from GET /v1/models, every page of it. Raises ModelError as a call does,
    without trying again: a person is waiting."""
    client = _client(key, base_url, timeout, 0)
    return _calling(lambda: [m.id for m in client.models.list(limit=MODELS_PAGE)], sendable(key))
