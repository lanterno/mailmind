"""What Accounts needs from outside it. Each is bound in mailmind.bootstrap.accounts."""
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from mailmind.accounts.domain.account import Credential, Entry


class AccountRepository(Protocol):
    """The configuration file. `read` gives a copy of the caller's own."""
    def read(self) -> Entry: ...
    def write(self, raw: Entry) -> None: ...


class SecretResolver(Protocol):
    """A credential, in any style the file allows."""
    def resolve(self, cred: Credential, *, who: str, strip: bool = False) -> str: ...
    def evict(self, cred: Credential) -> None: ...


class ConnectionProbe(Protocol):
    """A throwaway login to the account's server, and straight out again, which is the test: it shares no IMAP state,
    selects no folder, uses no kept connection and goes outside every queue and gate. It raises what a login raises."""
    def __call__(self, acct: Entry, timeout: int) -> None: ...


class SyncRecord(Protocol):
    """What the Mailbox recorded of each account's syncs, and how its sync stands now: the fields AccountHealth holds."""
    def __call__(self) -> Iterable[Mapping[str, Any]]: ...
