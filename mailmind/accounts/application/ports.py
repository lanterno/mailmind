"""What Accounts needs from outside it. Each is bound in mailmind.bootstrap.accounts."""
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from mailmind.accounts.domain.account import Credential, Entry


class ConfigStore(Protocol):
    """The file. `read` gives a copy of the caller's own."""
    def read(self) -> Entry: ...
    def write(self, raw: Entry) -> None: ...


class Secrets(Protocol):
    """A credential, in any style the file allows."""
    def resolve(self, cred: Credential, *, who: str, strip: bool = False) -> str: ...
    def evict(self, cred: Credential) -> None: ...


class Session(Protocol):
    def logout(self) -> object: ...


class OpenSession(Protocol):
    """A logged-in connection to the account's server, which is the test."""
    def __call__(self, acct: Entry, timeout: int) -> Session: ...


class SyncRecord(Protocol):
    """What the Mailbox recorded of each account's syncs, and how its sync stands now: the fields AccountHealth holds."""
    def __call__(self) -> Iterable[Mapping[str, Any]]: ...
