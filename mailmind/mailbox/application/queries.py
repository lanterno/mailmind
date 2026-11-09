"""What the page lists: a folder, a conversation, and the views that aren't folders (Snoozed, Muted, Waiting, Reply Later).
Each read is of the database alone, but that a folder opening may start its sync."""
from typing import Any

from mailmind.kernel.errors import Refused
from mailmind.kernel.ids import INBOX, AccountId, FolderName, MessageKey, ThreadRef
from mailmind.kernel.when import ago, rel_time, rel_time_future
from mailmind.mailbox.application.folders import Folders
from mailmind.mailbox.application.ports import Accounts, Attention, StoredMail
from mailmind.mailbox.domain import listing
from mailmind.platform.db.uow import UnitOfWork


class Queries:
    def __init__(self, uow: UnitOfWork, store: StoredMail, attention: Attention, folders: Folders, accounts: Accounts):
        self._uow = uow
        self._store = store
        self._attention = attention
        self._folders = folders
        self._accounts = accounts

    def _emails(self) -> list[str]:
        return [str(a["email"]) for a in self._accounts.accounts()]

    def _over(self, account: str | None) -> str | list[str]:
        """What a view that isn't a folder reads: the account named, or with none named every account, as the Inboxes' list does."""
        return account or self._emails()

    def pk_of_copy(self, key: int, folder: str, uid: int | None = None) -> int | None:
        """The row the page's copy, a message's key, a folder and the copy's uid there, is now, or None if that copy isn't
        there (`StoredMail.copy_pk`). A pk is not stable: the page never holds one, and asks this at each request."""
        with self._uow.read() as c:
            return self._store.copy_pk(c, MessageKey(key), FolderName(folder), uid)

    def pk_of_message(self, key: int, folder: str | None = None, uid: int | None = None) -> int | None:
        """A row of the message the page names by key now, or None if no copy is left: its copy at `uid` in `folder`, else
        its copy in `folder`, where it has one, else another."""
        with self._uow.read() as c:
            return self._store.message_pk(c, MessageKey(key), None if folder is None else FolderName(folder), uid)

    def snoozed_in(self, account: str | None, offset: int, limit: int) -> dict[str, Any]:
        """The 'Snoozed' virtual view: every snoozed message in the account, or in every account, soonest wake first."""
        accounts = self._over(account)
        with self._uow.read() as c:
            rows = self._store.snoozed_rows(c, accounts, limit, offset)
            total = self._store.snoozed_count(c, accounts)
        items = [listing.snoozed_item(r, rel_time_future) for r in rows]
        return {"ok": True, "account": account, "folder": "__snoozed__", "items": items, "snoozed": True,
                **listing.window(items, total, offset)}


    def muted_in(self, account: str | None, offset: int, limit: int) -> dict[str, Any]:
        """The 'Muted' virtual view: one row per muted thread (its latest message), so the user
        can find + unmute. Pure-local read (no IMAP)."""
        with self._uow.read() as c:
            all_rows = self._attention.list_muted(c, self._over(account))
        total = len(all_rows)
        rows = all_rows[offset:offset + limit]
        items = [listing.muted_item(r, rel_time) for r in rows]
        return {"ok": True, "account": account, "folder": "__muted__", "items": items, "muted": True,
                **listing.window(items, total, offset)}


    def waiting_in(self, account: str | None, offset: int, limit: int) -> dict[str, Any]:
        """The 'Waiting' virtual view: one row per due follow-up thread (commitments). Pure-local."""
        with self._uow.read() as c:
            all_rows = self._attention.list_waiting(c, self._over(account))
        total = len(all_rows)
        rows = all_rows[offset:offset + limit]
        items = [listing.waiting_item(r, rel_time, ago) for r in rows]
        return {"ok": True, "account": account, "folder": "__waiting__", "items": items, "waiting": True,
                **listing.window(items, total, offset)}


    def waiting_on_others(self, limit: int = 100) -> dict[str, Any]:
        """Every account's sent threads still with no reply, the longest wait first. Pure-local."""
        emails = self._emails()
        with self._uow.read() as c:
            rows = self._attention.waiting_on_others(c, emails, limit)
        return {"ok": True, "items": [listing.waiting_item(r, rel_time, ago) for r in rows]}


    def replylater_in(self, account: str | None, offset: int, limit: int) -> dict[str, Any]:
        """The 'Reply Later' virtual view: one row per pinned thread, newest pin first (a stack)."""
        with self._uow.read() as c:
            all_rows = self._attention.list_replylater(c, self._over(account))
        total = len(all_rows)
        rows = all_rows[offset:offset + limit]
        items = [listing.replylater_item(r, rel_time) for r in rows]
        return {"ok": True, "account": account, "folder": "__replylater__", "items": items, "replylater": True,
                **listing.window(items, total, offset)}


    def messages_in(self, account: AccountId | None, folder: FolderName, offset: int, limit: int, group: bool = False) -> dict[str, Any]:
        """A list by folder. A view that isn't a folder reads every account when none is named; a real folder is one account's."""
        if folder == "__snoozed__":
            return self.snoozed_in(account, offset, limit)
        if folder == "__muted__":
            return self.muted_in(account, offset, limit)
        if folder == "__waiting__":
            return self.waiting_in(account, offset, limit)
        if folder == "__replylater__":
            return self.replylater_in(account, offset, limit)
        if account is None:
            return {"ok": True, "account": None, "folder": folder, "items": [], "grouped": group, "total": 0, "offset": offset,
                    "has_more": False}
        scan: dict[str, bool] = self._folders.on_open(account, folder) if offset == 0 else {}
        with self._uow.read() as c:
            if group:
                rows = self._store.folder_conversation_rows(c, account, folder, limit, offset)
                total = self._store.folder_conversation_count(c, account, folder)
            else:
                rows = self._store.folder_rows(c, account, folder, limit, offset)
                total = self._store.folder_count(c, account, folder)
        items = [listing.listed_item(r, group, rel_time) for r in rows]
        return {"ok": True, "account": account, "folder": folder, "items": items, "grouped": group,
                **listing.window(items, total, offset), **scan}


    def labels(self, account: str | None) -> dict[str, Any]:
        """A Gmail account's own labels and how many messages carry each: its lanes in the rail. None on any other account."""
        if not account:
            raise Refused("account required")
        with self._uow.read() as c:
            rows = self._store.labels_of_account(c, AccountId(account))
        return {"ok": True, "labels": [{"name": r["name"], "count": r["count"]} for r in rows]}

    def all_inboxes(self, offset: int, limit: int, group: bool = False) -> dict[str, Any]:
        """Every account's Inbox as one list, newest first, each row naming its account. It answers from what is stored, so
        an account that's away or still on its first scan holds up no one."""
        emails = self._emails()
        scanning = [e for e in emails if self._folders.scanning_first(AccountId(e), INBOX)] if offset == 0 else []
        with self._uow.read() as c:
            if group:
                rows = self._store.inboxes_conversation_rows(c, emails, limit, offset)
                total = self._store.inboxes_conversation_count(c, emails)
            else:
                rows = self._store.inboxes_rows(c, emails, limit, offset)
                total = self._store.inboxes_count(c, emails)
        items = [listing.listed_item(r, group, rel_time) for r in rows]
        return {"ok": True, "folder": INBOX, "items": items, "grouped": group, **listing.window(items, total, offset),
                **({"syncing": True} if scanning else {})}


    def thread_json(self, account: AccountId, thread_id: ThreadRef) -> dict[str, Any]:
        """All messages in a conversation within an account, oldest->newest, de-duped by
        Message-ID (so a Gmail INBOX+All-Mail copy shows once). Raises Refused when either isn't named."""
        if not account or not thread_id:
            raise Refused("missing account/thread")
        with self._uow.read() as c:
            rows = self._store.thread_rows(c, account, thread_id)
        items = listing.thread_items(rows, rel_time)
        return {"ok": True, "account": account, "thread_id": thread_id,
                "items": items, "count": len(items), "reply_key": items[-1]["key"] if items else None}
