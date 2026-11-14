"""What Accounts needs from outside it. Each is bound in mailmind.bootstrap.accounts."""
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from mailmind.accounts.domain.account import Entry
from mailmind.accounts.domain.credential import Credential


class AccountRepository(Protocol):
    """The configuration file. `read` gives a copy of the caller's own."""
    def read(self) -> Entry: ...
    def write(self, raw: Entry) -> None: ...


class SecretStore(Protocol):
    """Where a credential's secret is: having it, in any style the file allows, and keeping one in the login Keychain or
    taking it out, which raises mailmind.platform.keychain's KeychainProblem when the Keychain won't."""
    def resolve(self, cred: Credential, *, who: str, strip: bool = False) -> str: ...
    def evict(self, cred: Credential) -> None: ...

    def keep(self, item: str, secret: str) -> None:
        """Keep `secret` as `item`, in place of what was there."""
        ...

    def discard(self, item: str) -> bool:
        """Take `item` out. False when there was none."""
        ...

    def holds(self, item: str) -> bool:
        """Whether the Keychain holds `item`."""
        ...


class ConnectionProbe(Protocol):
    """A throwaway login to the account's server, and straight out again, which is the test: it shares no IMAP state,
    selects no folder, uses no kept connection and goes outside every queue and gate. It raises what a login raises."""
    def saved(self, acct: Entry, timeout: int) -> None:
        """Someone asked, so it goes though a refused login is being held back, and what the server says counts."""
        ...

    def draft(self, acct: Entry, timeout: int) -> None:
        """What the server says of what was typed says nothing of any account saved."""
        ...


class SyncRecord(Protocol):
    """What the Mailbox recorded of each account's syncs, and how its sync stands now: the fields AccountHealth holds."""
    def __call__(self) -> Iterable[Mapping[str, Any]]: ...
