"""The settings page's question: how does each account stand?"""
from typing import Any

from mailmind.accounts.application.ports import SyncRecord
from mailmind.accounts.domain.health import AccountHealth
from mailmind.kernel.when import now_iso


class Health:
    def __init__(self, syncs: SyncRecord):
        self._syncs = syncs

    def answer(self) -> dict[str, Any]:
        """Each account's durable sync health, merged with what its sync is doing now. Pure read: no IMAP, no IMAP gate."""
        return {"ok": True, "accounts": [AccountHealth.of(s).as_row() for s in self._syncs()], "generated": now_iso()}
