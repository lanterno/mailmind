"""What Discovery needs from outside it. Each is bound in mailmind.bootstrap.discovery."""
from collections.abc import Iterable
from typing import Protocol

from mailmind.discovery.domain.known import Facts
from mailmind.discovery.domain.query import QuerySpec
from mailmind.discovery.domain.views import View
from mailmind.kernel.row import Row


class ViewsFile(Protocol):
    """Where a person's saved views are kept. What it holds is whatever the file held."""
    def read(self) -> object: ...
    def write(self, user_views: list[View]) -> None: ...


class SearchIndex(Protocol):
    """Stored mail as a query finds it. See adapters.sqlite."""
    def search(self, spec: QuerySpec, account: str | None, sort: str, limit: int, offset: int) -> list[Row] | None: ...
    def for_asking(self, match: str, accounts: str | list[str], limit: int) -> list[Row] | None: ...
    def reach(self, spec: QuerySpec, cap: int, apart: set[str]) -> tuple[int, list[Row], int] | None: ...
    def total(self) -> int: ...


class StoredDocuments(Protocol):
    """The attachments of stored mail, newest first. See adapters.sqlite."""
    def attachments(self, accounts: Iterable[str]) -> list[Row]: ...


class ContactMail(Protocol):
    """Stored mail by who it came from and went to. See adapters.sqlite."""
    def senders_like(self, q: str) -> list[Row]: ...
    def card(self, a: str) -> tuple[Row, list[str], list[Row]]: ...
    def known_facts(self, forms: list[str], min_history: int) -> Facts: ...


class KeptSenders(Protocol):
    """Whom the rule book keeps. A domain rule isn't knowing a person, so only senders count."""
    def kept(self) -> set[str]: ...


class AccountNames(Protocol):
    """The accounts as the rest of the app names them."""
    def emails(self) -> list[str]: ...
    def aliases(self) -> dict[str, str | None]: ...


class FolderRoles(Protocol):
    """Which folder plays which role: by what the last LIST said and else by name, or by name alone."""
    def role(self, account: str, folder: str) -> str: ...
    def role_by_name(self, folder: str) -> str: ...
