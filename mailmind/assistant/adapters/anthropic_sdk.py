"""The Messages API through the official Anthropic SDK: the new front door's language model.

The client is given its key and base URL, so the SDK reads no credential or profile of its own
from the environment or the disk. It retries the statuses in ports.RETRIED, twice, honouring
retry-after, and raises typed errors, which become ModelError here. The server-side refusal
fallback is a beta, so a prompt that asks for it goes through client.beta.messages.

Only this module imports anthropic, and only the new front door loads it: `python3 server.py`
runs with nothing installed.
"""
import anthropic

from mailmind.assistant.adapters import wire
from mailmind.assistant.application.ports import ModelError, Prompt, Reply
from mailmind.assistant.domain.models import FALLBACK_BETA
from mailmind.platform.db import txguard

TIMEOUT = 120                      # seconds per attempt
RETRIES = 2                        # after the first attempt
DEFAULT_URL = "https://api.anthropic.com"


def _error_text(e: anthropic.APIStatusError) -> str:
    body = e.body if isinstance(e.body, dict) else {}
    inner = body.get("error")
    err = inner if isinstance(inner, dict) else body
    return str(err.get("message") or e.message or "")[:200]


class AnthropicModel:
    enabled = True

    def __init__(self, key: str, base_url: str | None = None, timeout: float = TIMEOUT,
                 retries: int = RETRIES):
        self._client = anthropic.Anthropic(api_key=key, base_url=base_url or DEFAULT_URL,
                                           timeout=timeout, max_retries=retries)

    def _calling(self, call):
        """Make a call on this thread, as the SDK's retries are, its errors as ModelError."""
        txguard.refuse_network("a Claude API request")
        try:
            return call()
        except anthropic.APIStatusError as e:
            raise ModelError(f"Claude API {e.status_code}: {_error_text(e)}", e.status_code) from None
        except anthropic.APIConnectionError as e:
            raise ModelError(f"Claude API unreachable: {e.message}") from None
        except anthropic.AnthropicError as e:
            raise ModelError(f"Claude API failed: {type(e).__name__}") from None

    def complete(self, prompt: Prompt) -> Reply:
        body = wire.body(prompt)
        if body.pop("fallbacks", None):
            resp = self._calling(lambda: self._client.beta.messages.create(**body, fallbacks="default",
                                                                           betas=[FALLBACK_BETA]))
        else:
            resp = self._calling(lambda: self._client.messages.create(**body))
        return wire.reply(resp.to_dict(), prompt.model)
