"""The models a provider offers, for a person choosing one in Settings (#49), asked of the provider with the key kept for it,
at the URL a person chose for it while it is the provider chosen, else its own. It sends no mail and spends nothing, so it
goes with AI turned off too: the person is setting AI up. What can't be asked is refused in words, and a provider that
didn't answer is Unreachable."""
from collections.abc import Callable
from typing import Any

from mailmind.assistant.application.ports import ModelError, ModelLister
from mailmind.assistant.domain.providers import PROVIDERS, chat_models
from mailmind.kernel.ai_provider import AiProvider
from mailmind.kernel.errors import Refused, Unreachable

_KEY_REFUSED = frozenset({401, 403})


class Catalog:
    def __init__(self, key: Callable[[AiProvider], str], base_url: Callable[[AiProvider], str], lister: ModelLister):
        self._key, self._base_url, self._lister = key, base_url, lister

    def models(self, provider: AiProvider) -> dict[str, Any]:
        """{ok, models, default}: the ids `provider` lists that a person may choose, and the one it asks unless they do
        (None where they must)."""
        p = PROVIDERS[provider]
        base_url = self._base_url(provider) or p.base_url
        if not base_url:
            raise Refused("Give the service's URL first.")
        key = "" if p.key == "none" else self._key(provider)
        if p.key == "needed" and not key:
            raise Refused("Keep a key for this provider first.")
        try:
            ids = self._lister(p.wire, provider, base_url, key)
        except ModelError as e:
            if e.status in _KEY_REFUSED:
                raise Refused("The provider refused the key.") from None
            raise Unreachable("Couldn't reach the provider.") from None
        return {"ok": True, "models": chat_models(provider, ids), "default": p.model or None}
