"""Which logins are being held back after their server refused them (mailmind.accounts.domain.login_hold).

The Mailbox's open_session asks here before it connects, and publishes CredentialsRejected when a server says no. A
hold ends when its time is up, when a login works, when the account changes, and when someone asks to try now. A
start ends it too: nothing here is durable, and one more try after a restart is no lockout.
"""
import threading
import time
from collections.abc import Mapping
from typing import Any

from mailmind.accounts.domain import login_hold
from mailmind.accounts.domain.events import AccountChanged, AccountRemoved, CredentialsRejected
from mailmind.platform.logging import get_logger

log = get_logger("accounts")


class Logins:
    def __init__(self) -> None:
        self._holds: dict[str, login_hold.Hold] = {}      # email -> the last refusal's hold, kept past its end for the next
        self._lock = threading.Lock()

    def held(self, acct: Mapping[str, Any]) -> bool:
        """Whether a login as `acct` says must wait: nothing is to be sent to its server."""
        with self._lock:
            hold = self._holds.get(acct["email"])
        return hold is not None and hold.stops(login_hold.login_id(acct), time.time())

    def rejected(self, refused: CredentialsRejected) -> None:
        """CredentialsRejected: hold this login back."""
        with self._lock:
            hold = login_hold.after_refusal(self._holds.get(refused.email), refused.login, time.time())
            self._holds[refused.email] = hold
        log.warning("%(account)s: its server refused the login, and the next try waits %(count)d seconds",
                    {"account": refused.email, "count": hold.rest})

    def accepted(self, email: str) -> None:
        """A login worked."""
        self.forget(email)

    def changed(self, event: AccountChanged | AccountRemoved) -> None:
        """AccountChanged or AccountRemoved: someone looked at this account, so try what it says now."""
        self.forget(event.email)

    def forget(self, email: str) -> None:
        """Let the next login go, as when someone asks to try now."""
        with self._lock:
            self._holds.pop(email, None)
