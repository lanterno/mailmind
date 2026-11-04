"""The Assistant's wiring: which language model the process talks to, and with which key.

Each front door binds its own. The new one, `python -m mailmind serve`, talks to the API through
the Anthropic SDK: front.build calls use("sdk"). Everything else, `python3 server.py`, the command
line scripts and the root test suites, gets the standard-library client, since the old door must
run with nothing installed. Either way, with no key the model is DisabledModel: AI is off and
nothing is sent.

The key is config.anthropic_key() (mailmind.config.json's "anthropic_api_key", in any credential
style), and only then ANTHROPIC_API_KEY. A config key that won't resolve is no key, never a quiet
switch to the environment's. Both are read at each call, so a new key takes effect at once, and the
model is made again only when the key, ANTHROPIC_BASE_URL or the transport changes.
"""
import os
import threading

import config
from mailmind.assistant.adapters.disabled import DisabledModel
from mailmind.assistant.adapters.urllib_http import UrllibModel
from mailmind.assistant.application.gateway import Gateway

CONFIG = "mailmind.config.json"
ENV = "ANTHROPIC_API_KEY"


def key() -> tuple[str, str]:
    """(the API key, where it came from), the key "" when there is none. The config file wins,
    so a MailMind-only key isn't shadowed by one the shell exports for other tools."""
    try:
        k = config.anthropic_key()
    except Exception:
        return "", CONFIG
    if k is not None:
        return k, CONFIG
    return os.environ.get(ENV, "").strip(), ENV


def _sdk(api_key, base_url, **kw):
    from mailmind.assistant.adapters.anthropic_sdk import AnthropicModel   # the new door's only
    return AnthropicModel(api_key, base_url, **kw)


TRANSPORTS = {"urllib": UrllibModel, "sdk": _sdk}     # each (key, base_url, timeout=, retries=)
NAMES = {"urllib": "urllib", "sdk": "the Anthropic SDK"}


class _Bound:
    """The LanguageModel for the key, base URL and transport in force, made once for each."""

    def __init__(self, transport):
        self.transport = transport
        self._made = None, None
        self._lock = threading.Lock()

    def __call__(self):
        api_key = key()[0]
        made_for = (self.transport, api_key, os.environ.get("ANTHROPIC_BASE_URL") or None)
        with self._lock:
            if self._made[0] != made_for:
                transport, api_key, base_url = made_for
                model = TRANSPORTS[transport](api_key, base_url) if api_key else DisabledModel()
                self._made = made_for, model
            return self._made[1]


_bound = _Bound("urllib")
GATEWAY = Gateway(_bound)


def gateway() -> Gateway:
    return GATEWAY


def use(transport: str) -> None:
    """Talk to the API through `transport`: "sdk" or "urllib"."""
    if transport not in TRANSPORTS:
        raise ValueError(f"no transport {transport!r}")
    _bound.transport = transport


def transport() -> str:
    """How this process reaches the API, in words for the startup line."""
    return NAMES[_bound.transport]
