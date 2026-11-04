"""The gateway every AI call goes through: it builds the request for the model in use, from what
the domain knows about that model, and sends it through the LanguageModel bootstrap bound."""
from collections.abc import Callable

from mailmind.assistant.application.ports import LanguageModel, Prompt, Reply
from mailmind.assistant.domain import models
from mailmind.assistant.domain.schema import structured

MAX_TOKENS = 16000                 # a ceiling, not a target; adaptive thinking counts against it


class Gateway:
    def __init__(self, model: Callable[[], LanguageModel]):
        self._model = model        # called at each use: the model for the key in force now

    @property
    def on(self) -> bool:
        """False when AI is off: there is no key, and every call raises AIOff unsent."""
        return self._model().enabled

    def ask(self, text: str, schema: dict | None = None, *, model: str | None = None,
            effort: str | None = None) -> Reply:
        """Send `text` as one user turn to `model` (the default one unless named), with structured
        output when `schema` is given, at `effort` (medium unless named) where the model takes
        one, and the refusal fallback where it takes that. Raises ModelError when no reply came."""
        name = model or models.DEFAULT
        prompt = Prompt(text=text, model=name, max_tokens=MAX_TOKENS,
                        schema=None if schema is None else structured(schema),
                        effort=(effort or models.EFFORT) if models.takes_effort(name) else None,
                        fallback=models.takes_fallback(name))
        return self._model().complete(prompt)
