"""The Messages API over urllib, for the command line.

The same request as the SDK's adapter (wire.body), the same reply (wire.reply), and the SDK's retries,
on the statuses in ports.RETRIED and on network errors, honouring retry-after.

Standard library only.
"""
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request

from mailmind.assistant.adapters import wire
from mailmind.assistant.application.ports import RETRIED, BatchResult, ModelError, Prompt, Reply
from mailmind.assistant.domain.models import FALLBACK_BETA
from mailmind.platform.db import txguard

VERSION = "2026-10-21"             # anthropic-version header, the one the SDK sends
TIMEOUT = 120                      # seconds per attempt
RETRIES = 2                        # after the first attempt
DEFAULT_URL = "https://api.anthropic.com"
MESSAGES, BATCHES = "/v1/messages", "/v1/messages/batches"


def _retry_after(headers):
    try:
        return min(30.0, max(0.0, float(headers.get("retry-after"))))
    except (TypeError, ValueError):
        return None


def _error_text(e):
    try:
        return json.loads(e.read())["error"]["message"][:200]
    except Exception:
        return e.reason or ""


class UrllibModel:
    enabled = True

    def __init__(self, key: str, base_url: str | None = None, timeout: float = TIMEOUT,
                 retries: int = RETRIES):
        self._key = key
        self._base = (base_url or DEFAULT_URL).rstrip("/")
        self._timeout = timeout
        self._retries = retries

    def _headers(self) -> dict:
        return {"content-type": "application/json", "x-api-key": self._key, "anthropic-version": VERSION}

    def complete(self, prompt: Prompt) -> Reply:
        headers = self._headers()
        if prompt.fallback:
            headers["anthropic-beta"] = FALLBACK_BETA
        out = self._send("POST", MESSAGES, json.dumps(wire.body(prompt)).encode(), headers, self._retries)
        return wire.reply(json.loads(out), prompt.model)

    def submit_batch(self, requests: list[tuple[str, Prompt]]) -> str:
        data = json.dumps({"requests": [wire.batch_request(i, p) for i, p in requests]}).encode()
        return json.loads(self._send("POST", BATCHES, data, self._headers(), 0))["id"]

    def _batch_path(self, batch_id: str) -> str:
        return f"{BATCHES}/{urllib.parse.quote(batch_id, safe='')}"

    def batch_ended(self, batch_id: str) -> bool:
        out = self._send("GET", self._batch_path(batch_id), None, self._headers(), self._retries)
        return json.loads(out).get("processing_status") == "ended"

    def batch_results(self, batch_id: str, asked: str) -> list[BatchResult]:
        out = self._send("GET", self._batch_path(batch_id) + "/results", None, self._headers(), self._retries)
        return [wire.batch_result(json.loads(line), asked) for line in out.splitlines() if line.strip()]

    def discard_batch(self, batch_id: str) -> None:
        self._send("DELETE", self._batch_path(batch_id), None, self._headers(), self._retries)

    def _send(self, method: str, path: str, data: bytes | None, headers: dict, retries: int) -> bytes:
        attempt = 0
        while True:
            txguard.refuse_network("a Claude API request")
            req = urllib.request.Request(self._base + path, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code not in RETRIED or attempt == retries:
                    raise ModelError(f"Claude API {e.code}: {_error_text(e)}", e.code) from None
                wait = _retry_after(e.headers)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt == retries:
                    raise ModelError(f"Claude API unreachable: {getattr(e, 'reason', e)}") from None
                wait = None
            time.sleep(wait if wait is not None else min(8.0, 0.5 * 2 ** attempt) + random.random() / 4)
            attempt += 1
