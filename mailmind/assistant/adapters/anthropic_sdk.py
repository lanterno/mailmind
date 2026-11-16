"""The Messages API through the official Anthropic SDK: Claude, the default language model the server and every command talk to.

The client is given its key and base URL, so the SDK reads no credential or profile of its own
from the environment or the disk. It retries the statuses in ports.RETRIED, twice, honouring
retry-after, and raises typed errors, which become ModelError here, with what the API said of an
error: its message as words, or as compact JSON where it is anything else, the key taken out of all
of it before it is cut to SAID characters. The server-side refusal fallback is a beta, so a prompt
that asks for it goes through client.beta.messages. A key with a blank or a control character in it
is never sent: it is refused as a rejected key is.

The SDK sends nothing itself: each of its attempts is an `exchange` with the endpoint the client was
made for (`_Exchange`), as every other provider's are. So no proxy and no header the environment names
steers a call, TLS is verified as every other provider's is rather than on httpx's bundle, no redirect
is followed, an attempt's time limit is a deadline for all of it, and an answer runs to exchange.MOST
bytes at most. A URL that names no API is unreachable, with nothing sent.

Only this module imports anthropic, and httpx2, the SDK's HTTP library, whose client it is handed.
"""
import json
from collections.abc import Callable

import anthropic
import httpx2

from mailmind.assistant.adapters import exchange, wire
from mailmind.assistant.application.ports import BatchResult, ModelError, Prompt, Reply
from mailmind.assistant.domain import models
from mailmind.assistant.domain.models import FALLBACK_BETA
from mailmind.assistant.domain.providers import TIMEOUT, sendable
from mailmind.platform.db import txguard

RETRIES = 2                        # after the first attempt
DEFAULT_URL = "https://api.anthropic.com"
VERSION = "2026-10-21"             # the API version every request names, as the SDK's own does
MODELS_PAGE = 1000                 # the most the API lists at once
KEY_REFUSED = "Claude API 401: the key was refused"     # for a key that can't be one, never sent
UNREACHABLE = "Claude API unreachable: no API address"  # for a URL that names no API, where nothing is sent
SAID = 200                         # characters of what the API said an error keeps
_JSON, _BINARY = "application/json", "application/binary"     # what a request accepts: a batch's results are the latter


class _Exchange(httpx2.BaseTransport):
    """How the SDK's requests go: each an `exchange` attempt at the one endpoint the client was made for, with the headers
    the API reads made here, the key and the API version among them, and no other: none the environment names (the SDK
    reads custom headers and a bearer token from it), and none of the SDK's about the platform. A URL the API names on
    another host, a batch's results_url, is asked of this endpoint, at the same path under its own."""

    def __init__(self, end: exchange.Endpoint, key: str, timeout: float):
        self._end, self._key, self._timeout = end, key, timeout

    def _elsewhere(self, url: httpx2.URL) -> bool:
        end = self._end
        return (url.scheme, url.host, url.port or (443 if url.scheme == "https" else 80)) != (
            "https" if end.tls else "http", end.host, end.port)

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        url = request.url
        target = url.raw_path.decode("ascii")
        if self._elsewhere(url):
            target = self._end.base + target
        body = request.read() or None
        headers = {"Accept": _BINARY if request.headers.get("accept") == _BINARY else _JSON, "Anthropic-Version": VERSION,
                   "X-Api-Key": self._key}
        if body is not None:
            headers["Content-Type"] = _JSON
        if request.headers.get("anthropic-beta") == FALLBACK_BETA:            # the one beta asked, and none other
            headers["Anthropic-Beta"] = FALLBACK_BETA
        try:
            answer = exchange.attempt(self._end, request.method, target, headers, body, self._timeout)
        except exchange.Unanswered as e:          # the SDK's own: a timeout, or a connection error, tried again
            lost = httpx2.ReadTimeout if e.why == "timed out" else httpx2.ConnectError
            raise lost(e.why, request=request) from None
        return httpx2.Response(answer.status, headers=list(answer.headers.items()), content=answer.body, request=request)


def _error_text(e: anthropic.APIStatusError, key: str) -> str:
    """What the API said of an error: its error's message, else the body's, else the SDK's words for it; words as they are
    and anything else as compact JSON, with `key` taken out of all of it before it is cut to SAID characters, so no part of
    a key the cut runs through is left."""
    body = e.body if isinstance(e.body, dict) else {}
    inner = body.get("error")
    err = inner if isinstance(inner, dict) else body
    said = err.get("message")
    if not said:
        said = e.message or ""
    text = said if isinstance(said, str) else json.dumps(said, separators=(",", ":"), ensure_ascii=False)
    return (text.replace(key, "<key>") if key else text)[:SAID]


def _calling[T](call: Callable[[anthropic.Anthropic], T], client: anthropic.Anthropic | None, key: str) -> T:
    """Make a call with `client` on this thread, as the SDK's retries are, its errors as ModelError: none with no client,
    for a URL that names no API, or with a key that isn't sendable."""
    txguard.refuse_network("a Claude API request")
    if client is None:
        raise ModelError(UNREACHABLE)
    if not sendable(key):
        raise ModelError(KEY_REFUSED, 401)
    try:
        return call(client)
    except anthropic.APIStatusError as e:
        raise ModelError(f"Claude API {e.status_code}: {_error_text(e, key)}", e.status_code) from None
    except anthropic.APIConnectionError as e:
        raise ModelError(f"Claude API unreachable: {e.message}") from None
    except anthropic.AnthropicError as e:
        raise ModelError(f"Claude API failed: {type(e).__name__}") from None


def _client(key: str, base_url: str | None, timeout: float, retries: int) -> anthropic.Anthropic | None:
    """The SDK's client for the API at `base_url` (DEFAULT_URL where it names none), its attempts made by `_Exchange`; None
    for a URL that names no API."""
    url = base_url or DEFAULT_URL
    end = exchange.endpoint(url)
    if end is None:
        return None
    http = httpx2.Client(transport=_Exchange(end, key, timeout), trust_env=False, follow_redirects=False, timeout=timeout)
    return anthropic.Anthropic(api_key=key, base_url=url, timeout=timeout, max_retries=retries, http_client=http)


class AnthropicModel:
    enabled = True
    takes_batches = True

    def __init__(self, key: str, base_url: str | None = None, timeout: float = TIMEOUT,
                 retries: int = RETRIES, model: str = models.DEFAULT, local: bool = False):
        self.model, self.local = model, local
        self._client = _client(key, base_url, timeout, retries)
        self._key = key

    def complete(self, prompt: Prompt) -> Reply:
        body = wire.body(prompt)
        if prompt.fallback:
            resp = _calling(lambda c: c.beta.messages.create(**body, fallbacks="default", betas=[FALLBACK_BETA]),
                            self._client, self._key)
        else:
            resp = _calling(lambda c: c.messages.create(**body), self._client, self._key)
        return wire.reply(resp.to_dict(), prompt.model)

    def submit_batch(self, requests: list[tuple[str, Prompt]]) -> str:
        return _calling(lambda c: c.with_options(max_retries=0).messages.batches.create(
            requests=[wire.batch_request(i, p) for i, p in requests]),  # pyright: ignore[reportArgumentType]
            self._client, self._key).id

    def batch_ended(self, batch_id: str) -> bool:
        batch = _calling(lambda c: c.messages.batches.retrieve(batch_id), self._client, self._key)
        return batch.processing_status == "ended"

    def batch_results(self, batch_id: str, asked: str) -> list[BatchResult]:
        return _calling(lambda c: [wire.batch_result(line.to_dict(), asked) for line in c.messages.batches.results(batch_id)],
                        self._client, self._key)

    def discard_batch(self, batch_id: str) -> None:
        _calling(lambda c: c.messages.batches.delete(batch_id), self._client, self._key)


def list_models(key: str, base_url: str | None = None, timeout: float = TIMEOUT) -> list[str]:
    """The ids of the models the key may ask, from GET /v1/models, every page of it. Raises ModelError as a call does,
    without trying again: a person is waiting."""
    return _calling(lambda c: [m.id for m in c.models.list(limit=MODELS_PAGE)], _client(key, base_url, timeout, 0), key)
