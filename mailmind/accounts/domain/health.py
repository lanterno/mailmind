"""How an account stands, from what its syncs came to. The Mailbox reports what happened, and this says what it means."""
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

type ErrorKind = Literal["TIMEOUT", "AUTH", "UIDVALIDITY", "NETWORK", "TLS_PIN", "IMAP", "UNKNOWN"]
type Live = Literal["ok", "syncing", "error", ""]        # how its sync stands now; "" when none has run since the start


@dataclass(frozen=True)
class AccountHealth:
    """An account's last sync that worked and last that failed, as the Mailbox recorded them, and how its sync stands
    now. Of an error it holds the class's name and its kind, never what it said."""
    account: str
    last_ok_ts: str | None
    last_error_ts: str | None
    last_error_kind: str | None        # as stored, so an older row may say what no kind says now
    last_error: str | None
    sync_count: int
    live: Live

    @classmethod
    def of(cls, sync: Mapping[str, Any]) -> "AccountHealth":
        return cls(sync["account"], sync["last_ok_ts"], sync["last_error_ts"], sync["last_error_kind"], sync["last_error"],
                   sync["sync_count"], sync["live"])

    @property
    def healthy(self) -> bool:
        return bool(self.last_ok_ts) and not self.last_error

    def as_row(self) -> dict[str, Any]:
        return {"account": self.account, "last_ok_ts": self.last_ok_ts, "last_error_ts": self.last_error_ts,
                "last_error_kind": self.last_error_kind, "last_error": self.last_error, "sync_count": self.sync_count,
                "healthy": self.healthy, "live": self.live}


def error_kind(e: BaseException) -> ErrorKind:
    """Coarse, scrubbed error kind for the health surface — derived from the exception TYPE only,
    never its message (which can carry credentials/host strings)."""
    n = type(e).__name__.lower()
    if n == "tlspinmismatch":
        return "TLS_PIN"
    if "timeout" in n:
        return "TIMEOUT"
    if any(w in n for w in ("auth", "login", "permission")):
        return "AUTH"
    if "uidvalidity" in n:
        return "UIDVALIDITY"
    if any(w in n for w in ("gaierror", "connection", "socket", "ssl", "dns", "resolve")):
        return "NETWORK"
    if "imap" in n:
        return "IMAP"
    return "UNKNOWN"
