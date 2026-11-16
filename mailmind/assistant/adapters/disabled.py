"""The language model while AI is off: turned off in Settings, or with no key, URL or model to ask. Nothing is ever sent, and
every call raises AIOff, whose words say why and never hold a secret."""
from mailmind.assistant.application.ports import AIOff, BatchResult, Prompt, Reply
from mailmind.assistant.domain import models
from mailmind.assistant.domain.providers import AiOff

WHY: dict[AiOff, str] = {"turned-off": "AI is off: it is turned off in Settings",
                         "no-key": "AI is off: there is no API key",
                         "no-url": "AI is off: the service has no URL",
                         "no-model": "AI is off: no model is chosen"}


class DisabledModel:
    enabled = False
    local = False
    takes_batches = False

    def __init__(self, off: AiOff = "no-key", model: str = models.DEFAULT):
        self.off: AiOff = off
        self.model = model                   # what would be asked: the startup line names it

    def _refuse(self):
        return AIOff(WHY[self.off])

    def complete(self, prompt: Prompt) -> Reply:
        raise self._refuse()

    def submit_batch(self, requests: list[tuple[str, Prompt]]) -> str:
        raise self._refuse()

    def batch_ended(self, batch_id: str) -> bool:
        raise self._refuse()

    def batch_results(self, batch_id: str, asked: str) -> list[BatchResult]:
        raise self._refuse()

    def discard_batch(self, batch_id: str) -> None:
        raise self._refuse()
