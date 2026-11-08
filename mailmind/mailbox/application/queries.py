"""What the page lists: a folder, a conversation, and the views that aren't folders (Snoozed, Muted, Waiting, Reply Later).
Each read is of the database alone, but that a folder opening may start its sync."""
from typing import Any

from mailmind.kernel import attention as verdict
from mailmind.kernel.errors import Refused
from mailmind.kernel.ids import INBOX, AccountId, FolderName, MessageKey, ThreadRef
from mailmind.kernel.row import Row
from mailmind.kernel.when import ago, rel_time, rel_time_future
from mailmind.mailbox.application.folders import Folders
from mailmind.mailbox.application.ports import Accounts, Attention, StoredMail
from mailmind.platform.db.uow import UnitOfWork


def _attention(r: Row) -> dict[str, bool]:
    """What the verdict on row `r` asks of you. The page reads these and never works them out."""
    return verdict.answers(r["importance"], r["category"], r["action"])


def _listed_item(r: Row, group: bool) -> dict[str, Any]:
    """A row of a list of a folder or of the Inboxes, one conversation's latest message if `group`."""
    it: dict[str, Any] = {
        "key": r["message_key"], "folder": r["folder"], "uid": r["uid"], "from_name": r["from_name"] or r["from_addr"],
        "from_addr": r["from_addr"],
        "subject": r["subject"] or "(no subject)", "when": rel_time(r["date_iso"]),
        "snippet": (r["snippet"] or "")[:200], "seen": bool(r["seen"]), "flagged": bool(r["flagged"]),
        "has_attach": bool(r["has_attach"]), "is_junk": bool(r["is_junk"]), "bucket": r["bucket"],
        "importance": r["importance"], **_attention(r), "thread_id": r["thread_id"],
        "summary": (r["summary"] or "")[:300], "action": r["action"] or "", "category": r["category"],
        "ts": r["ts"], "account": r["account"],
    }
    if group:
        it.update({"count": r["cnt"], "thread_count": r["thread_count"] or r["cnt"], "seen": not r["unread"],
                   "flagged": bool(r["any_flagged"]), "has_attach": bool(r["any_attach"])})
    return it


def _waiting_item(r: Row) -> dict[str, Any]:
    if r["f_kind"] == "sent":
        label = "Sent " + ago(r["f_first_sent"]) + " ago · no reply"
    else:
        label = "Awaiting your reply · " + rel_time(r["date_iso"])
    return {
        "key": r["message_key"], "folder": r["folder"], "uid": r["uid"],
        "from_name": r["from_name"] or r["from_addr"],
        "from_addr": r["from_addr"], "subject": r["subject"] or "(no subject)",
        "when": rel_time(r["date_iso"]), "snippet": (r["snippet"] or "")[:200],
        "seen": bool(r["seen"]), "flagged": bool(r["flagged"]), "has_attach": bool(r["has_attach"]),
        "is_junk": bool(r["is_junk"]), "bucket": r["bucket"], "thread_id": r["thread_id"],
        "ts": r["ts"], "count": r["thread_count"], "waiting": True,
        "waiting_label": label, "kind": r["f_kind"],
        "to": (r["recipients"] or "").split(",")[0].strip(),   # who a sent thread waits on
    }


class Queries:
    def __init__(self, uow: UnitOfWork, store: StoredMail, attention: Attention, folders: Folders, accounts: Accounts):
        self._uow = uow
        self._store = store
        self._attention = attention
        self._folders = folders
        self._accounts = accounts

    def _emails(self) -> list[str]:
        return [str(a["email"]) for a in self._accounts.accounts()]

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

    def snoozed_in(self, account: str, offset: int, limit: int) -> dict[str, Any]:
        """The 'Snoozed' virtual view: every snoozed message in the account, soonest wake first."""
        with self._uow.read() as c:
            rows = self._store.snoozed_rows(c, account, limit, offset)
            total = self._store.snoozed_count(c, account)
        items = [{
            "key": r["message_key"], "folder": r["folder"], "uid": r["uid"],
            "from_name": r["from_name"] or r["from_addr"],
            "from_addr": r["from_addr"], "subject": r["subject"] or "(no subject)",
            "when": "wakes " + rel_time_future(r["snoozed_until"]), "snoozed_until": r["snoozed_until"],
            "snippet": (r["snippet"] or "")[:200], "seen": bool(r["seen"]), "flagged": bool(r["flagged"]),
            "has_attach": bool(r["has_attach"]), "is_junk": bool(r["is_junk"]), "bucket": r["bucket"],
            "importance": r["importance"], **_attention(r), "thread_id": r["thread_id"],
        } for r in rows]
        return {"ok": True, "account": account, "folder": "__snoozed__", "items": items, "snoozed": True,
                "total": total, "offset": offset, "has_more": offset + len(items) < total}


    def muted_in(self, account: str, offset: int, limit: int) -> dict[str, Any]:
        """The 'Muted' virtual view: one row per muted thread (its latest message), so the user
        can find + unmute. Pure-local read (no IMAP)."""
        with self._uow.read() as c:
            all_rows = self._attention.list_muted(c, account)
        total = len(all_rows)
        rows = all_rows[offset:offset + limit]
        items = [{
            "key": r["message_key"], "folder": r["folder"], "uid": r["uid"],
            "from_name": r["from_name"] or r["from_addr"],
            "from_addr": r["from_addr"], "subject": r["subject"] or "(no subject)",
            "when": rel_time(r["date_iso"]), "snippet": (r["snippet"] or "")[:200],
            "seen": True, "flagged": bool(r["flagged"]), "has_attach": bool(r["has_attach"]),
            "is_junk": bool(r["is_junk"]), "bucket": r["bucket"], "thread_id": r["thread_id"],
            "ts": r["ts"], "count": r["thread_count"], "muted": True,
        } for r in rows]
        return {"ok": True, "account": account, "folder": "__muted__", "items": items, "muted": True,
                "total": total, "offset": offset, "has_more": offset + len(items) < total}


    def waiting_in(self, account: str, offset: int, limit: int) -> dict[str, Any]:
        """The 'Waiting' virtual view: one row per due follow-up thread (commitments). Pure-local."""
        with self._uow.read() as c:
            all_rows = self._attention.list_waiting(c, account)
        total = len(all_rows)
        rows = all_rows[offset:offset + limit]
        items = [_waiting_item(r) for r in rows]
        return {"ok": True, "account": account, "folder": "__waiting__", "items": items, "waiting": True,
                "total": total, "offset": offset, "has_more": offset + len(items) < total}


    def waiting_on_others(self, limit: int = 100) -> dict[str, Any]:
        """Every account's sent threads still with no reply, the longest wait first. Pure-local."""
        emails = self._emails()
        with self._uow.read() as c:
            rows = self._attention.waiting_on_others(c, emails, limit)
        return {"ok": True, "items": [{**_waiting_item(r), "account": r["account"]} for r in rows]}


    def replylater_in(self, account: str, offset: int, limit: int) -> dict[str, Any]:
        """The 'Reply Later' virtual view: one row per pinned thread, newest pin first (a stack)."""
        with self._uow.read() as c:
            all_rows = self._attention.list_replylater(c, account)
        total = len(all_rows)
        rows = all_rows[offset:offset + limit]
        items = [{
            "key": r["message_key"], "folder": r["folder"], "uid": r["uid"],
            "from_name": r["from_name"] or r["from_addr"],
            "from_addr": r["from_addr"], "subject": r["subject"] or "(no subject)",
            "when": rel_time(r["date_iso"]), "snippet": (r["snippet"] or "")[:200],
            "seen": bool(r["seen"]), "flagged": bool(r["flagged"]), "has_attach": bool(r["has_attach"]),
            "is_junk": bool(r["is_junk"]), "bucket": r["bucket"], "thread_id": r["thread_id"],
            "ts": r["ts"], "count": r["thread_count"], "replylater": True,
            "waiting_label": "Reply later",
        } for r in rows]
        return {"ok": True, "account": account, "folder": "__replylater__", "items": items, "replylater": True,
                "total": total, "offset": offset, "has_more": offset + len(items) < total}


    def messages_in(self, account: AccountId, folder: FolderName, offset: int, limit: int, group: bool = False) -> dict[str, Any]:
        if folder == "__snoozed__":
            return self.snoozed_in(account, offset, limit)
        if folder == "__muted__":
            return self.muted_in(account, offset, limit)
        if folder == "__waiting__":
            return self.waiting_in(account, offset, limit)
        if folder == "__replylater__":
            return self.replylater_in(account, offset, limit)
        scan: dict[str, bool] = self._folders.on_open(account, folder) if offset == 0 else {}
        with self._uow.read() as c:
            if group:
                rows = self._store.folder_conversation_rows(c, account, folder, limit, offset)
                total = self._store.folder_conversation_count(c, account, folder)
            else:
                rows = self._store.folder_rows(c, account, folder, limit, offset)
                total = self._store.folder_count(c, account, folder)
        items = [_listed_item(r, group) for r in rows]
        return {"ok": True, "account": account, "folder": folder, "items": items, "grouped": group,
                "total": total, "offset": offset, "has_more": offset + len(items) < total, **scan}


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
        items = [_listed_item(r, group) for r in rows]
        return {"ok": True, "folder": INBOX, "items": items, "grouped": group, "total": total, "offset": offset,
                "has_more": offset + len(items) < total, **({"syncing": True} if scanning else {})}


    def thread_json(self, account: AccountId, thread_id: ThreadRef) -> dict[str, Any]:
        """All messages in a conversation within an account, oldest->newest, de-duped by
        Message-ID (so a Gmail INBOX+All-Mail copy shows once). Raises Refused when either isn't named."""
        if not account or not thread_id:
            raise Refused("missing account/thread")
        with self._uow.read() as c:
            rows = self._store.thread_rows(c, account, thread_id)
        items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for r in rows:
            key = (r["message_id"] or "").strip() or f"pk:{r['pk']}"
            if key in seen_ids:
                continue
            seen_ids.add(key)
            items.append({
                "key": r["message_key"], "folder": r["folder"], "uid": r["uid"],
            "from_name": r["from_name"] or r["from_addr"],
                "from_addr": r["from_addr"], "subject": r["subject"] or "(no subject)",
                "when": rel_time(r["date_iso"]), "snippet": (r["snippet"] or "")[:200],
                "seen": bool(r["seen"]), "flagged": bool(r["flagged"]), "answered": bool(r["answered"]),
                "has_attach": bool(r["has_attach"]), "is_junk": bool(r["is_junk"]),
            })
        return {"ok": True, "account": account, "thread_id": thread_id,
                "items": items, "count": len(items), "reply_key": items[-1]["key"] if items else None}
