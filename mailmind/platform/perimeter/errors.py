"""What a check answers when a request may not go on: its status, and what a person is told. The middleware
(mailmind.http.middleware) answers it as a problem document."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Refusal:
    status: int
    error: str
