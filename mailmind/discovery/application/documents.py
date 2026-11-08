"""The Document Hub: one findable place for every real attachment (PDF/image/doc/…) across all accounts. Pure local SQL
over the attachments table (metadata only: filename/type/size, no body/IMAP fetch). Downloads reuse the existing
/api/message/attachment route, by the key and folder of the copy that holds the file."""
from collections.abc import Mapping
from typing import Any

from mailmind.discovery.application.ports import AccountNames, StoredDocuments
from mailmind.discovery.domain import documents
from mailmind.kernel.when import rel_time


class DocumentHub:
    def __init__(self, mail: StoredDocuments, accounts: AccountNames):
        self._mail = mail
        self._accounts = accounts

    def page(self, qs: Mapping[str, list[str]]) -> dict[str, Any]:
        """Read-only SQL, over the accounts every other corpus-wide endpoint goes over."""
        want = documents.asked(qs)
        enabled = set(self._accounts.emails())
        return documents.page(self._mail.attachments(enabled) if enabled else [], want, rel_time)
