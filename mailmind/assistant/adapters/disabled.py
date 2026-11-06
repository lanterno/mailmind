"""The language model when there is no API key: AI is off, and nothing is ever sent."""
from mailmind.assistant.application.ports import AIOff, BatchResult, Prompt, Reply


class DisabledModel:
    enabled = False

    def complete(self, prompt: Prompt) -> Reply:
        raise AIOff("AI is off: there is no API key")

    def submit_batch(self, requests: list[tuple[str, Prompt]]) -> str:
        raise AIOff("AI is off: there is no API key")

    def batch_ended(self, batch_id: str) -> bool:
        raise AIOff("AI is off: there is no API key")

    def batch_results(self, batch_id: str, asked: str) -> list[BatchResult]:
        raise AIOff("AI is off: there is no API key")

    def discard_batch(self, batch_id: str) -> None:
        raise AIOff("AI is off: there is no API key")
