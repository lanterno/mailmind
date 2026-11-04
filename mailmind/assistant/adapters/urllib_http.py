"""The Messages API over urllib, for the old front door.

`python3 server.py` is the kill switch while the new door carries daily use, and it runs with
nothing installed, so it can't load the Anthropic SDK. This is the client it runs instead: the same
request (wire.body), the same reply (wire.reply), and the SDK's retries, on the statuses in
ports.RETRIED and on network errors, honouring retry-after. It goes when the old door does.

Standard library only.
"""
import json
import random
import time
import urllib.error
import urllib.request

from mailmind.assistant.adapters import wire
from mailmind.assistant.application.ports import RETRIED, ModelError, Prompt, Reply
from mailmind.assistant.domain.models import FALLBACK_BETA
from mailmind.platform.db import txguard

VERSION = "2026-10-21"             # anthropic-version header, the one the SDK sends
TIMEOUT = 120                      # seconds per attempt
RETRIES = 2                        # after the first attempt
DEFAULT_URL = "https://api.anthropic.com"


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
        self._url = (base_url or DEFAULT_URL).rstrip("/") + "/v1/messages"
        self._timeout = timeout
        self._retries = retries

    def complete(self, prompt: Prompt) -> Reply:
        headers = {"content-type": "application/json", "x-api-key": self._key,
                   "anthropic-version": VERSION}
        if prompt.fallback:
            headers["anthropic-beta"] = FALLBACK_BETA
        return wire.reply(self._post(json.dumps(wire.body(prompt)).encode(), headers), prompt.model)

    def _post(self, data: bytes, headers: dict) -> dict:
        attempt = 0
        while True:
            txguard.refuse_network("a Claude API request")
            req = urllib.request.Request(self._url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code not in RETRIED or attempt == self._retries:
                    raise ModelError(f"Claude API {e.code}: {_error_text(e)}", e.code) from None
                wait = _retry_after(e.headers)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt == self._retries:
                    raise ModelError(f"Claude API unreachable: {getattr(e, 'reason', e)}") from None
                wait = None
            time.sleep(wait if wait is not None else min(8.0, 0.5 * 2 ** attempt) + random.random() / 4)
            attempt += 1
