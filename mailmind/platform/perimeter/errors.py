"""What a check answers when a request may not go on."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Refusal:
    status: int
    error: str

    def answer(self):
        """The JSON body every refusal answers with."""
        return {"ok": False, "error": self.error}
