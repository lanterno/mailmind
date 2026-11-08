"""What the search box offers as you type. Local SQL over the mail already stored, and it spends nothing: the model is never asked."""
from typing import Any

from mailmind.discovery.application.ports import ContactMail
from mailmind.discovery.domain import suggest


class Suggesting:
    def __init__(self, mail: ContactMail):
        self._mail = mail

    def answer(self, q: str | None) -> dict[str, Any]:
        """The suggestions for the box as it reads now. `/api/contacts` looks through the same senders."""
        t = suggest.typing(q)
        if t is None:
            return {"ok": True, "items": []}
        needle = suggest.needle(t)
        senders = self._mail.senders_like(needle) if needle is not None else []
        domains = self._mail.sender_domains_like(needle) if needle is not None else []
        return {"ok": True, "items": suggest.offers(t, senders, domains)}
