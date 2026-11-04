"""The language model when there is no API key: AI is off, and nothing is ever sent."""
from mailmind.assistant.application.ports import AIOff, Prompt, Reply


class DisabledModel:
    enabled = False

    def complete(self, prompt: Prompt) -> Reply:
        raise AIOff("AI is off: there is no API key")
