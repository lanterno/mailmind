"""What the page lists: a folder, a conversation, and the views that aren't folders (Snoozed, Muted, Waiting, Reply Later).
Each read is of the database alone, but that a folder opening may start its sync."""
from typing import Any

from mailmind.kernel import attention as verdict
from mailmind.kernel.row import Row
from mailmind.kernel.when import ago, rel_time, rel_time_future
from mailmind.mailbox.application.folders import Folders
from mailmind.mailbox.application.ports import Attention, StoredMail

_MUTED_EXCL = ("AND NOT EXISTS (SELECT 1 FROM muted mu WHERE mu.account={t}.account "
               "AND mu.thread_id={t}.thread_id)")   # mirror the snoozed_until IS NULL exclusion


def _attention(r: Row) -> dict[str, bool]:
    """What the verdict on row `r` asks of you. The page reads these and never works them out."""
    return verdict.answers(r["importance"], r["category"], r["action"])


class Queries:
    def __init__(self, store: StoredMail, attention: Attention, folders: Folders):
        self._store = store
        self._attention = attention
        self._folders = folders

    def snoozed_in(self, account: str, offset: int, limit: int) -> dict[str, Any]:
        """The 'Snoozed' virtual view: every snoozed message in the account, soonest wake first."""
        c = self._store.conn()
        rows = c.execute(
            """SELECT m.pk, m.message_key, m.folder, m.from_addr, m.from_name, m.subject, m.date_iso, m.snippet,
                      m.seen, m.flagged, m.has_attach, m.is_junk, m.bucket, m.thread_id,
                      m.snoozed_until, s.importance, s.category, s.action
               FROM messages m LEFT JOIN summaries s ON s.message_pk=m.pk
               WHERE m.account=? AND m.snoozed_until IS NOT NULL AND m.pending_removal IS NULL
               ORDER BY m.snoozed_until ASC LIMIT ? OFFSET ?""", (account, limit, offset)).fetchall()
        total = c.execute("SELECT COUNT(*) FROM messages WHERE account=? AND snoozed_until IS NOT NULL "
                            "AND pending_removal IS NULL",
                          (account,)).fetchone()[0]
        c.close()
        items = [{
            "pk": r["pk"], "key": r["message_key"], "folder": r["folder"],
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
        c = self._store.conn()
        all_rows = self._attention.list_muted(c, account)
        c.close()
        total = len(all_rows)
        rows = all_rows[offset:offset + limit]
        items = [{
            "pk": r["pk"], "key": r["message_key"], "folder": r["folder"],
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
        c = self._store.conn()
        all_rows = self._attention.list_waiting(c, account)
        c.close()
        total = len(all_rows)
        rows = all_rows[offset:offset + limit]
        items: list[dict[str, Any]] = []
        for r in rows:
            if r["f_kind"] == "sent":
                label = "Sent " + ago(r["f_first_sent"]) + " ago · no reply"
            else:
                label = "Awaiting your reply · " + rel_time(r["date_iso"])
            items.append({
                "pk": r["pk"], "key": r["message_key"], "folder": r["folder"],
            "from_name": r["from_name"] or r["from_addr"],
                "from_addr": r["from_addr"], "subject": r["subject"] or "(no subject)",
                "when": rel_time(r["date_iso"]), "snippet": (r["snippet"] or "")[:200],
                "seen": bool(r["seen"]), "flagged": bool(r["flagged"]), "has_attach": bool(r["has_attach"]),
                "is_junk": bool(r["is_junk"]), "bucket": r["bucket"], "thread_id": r["thread_id"],
                "ts": r["ts"], "count": r["thread_count"], "waiting": True,
                "waiting_label": label, "kind": r["f_kind"],
                "to": (r["recipients"] or "").split(",")[0].strip(),   # who a sent thread waits on
            })
        return {"ok": True, "account": account, "folder": "__waiting__", "items": items, "waiting": True,
                "total": total, "offset": offset, "has_more": offset + len(items) < total}


    def replylater_in(self, account: str, offset: int, limit: int) -> dict[str, Any]:
        """The 'Reply Later' virtual view: one row per pinned thread, newest pin first (a stack)."""
        c = self._store.conn()
        all_rows = self._attention.list_replylater(c, account)
        c.close()
        total = len(all_rows)
        rows = all_rows[offset:offset + limit]
        items = [{
            "pk": r["pk"], "key": r["message_key"], "folder": r["folder"],
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


    def messages_in(self, account: str, folder: str, offset: int, limit: int, group: bool = False) -> dict[str, Any]:
        if folder == "__snoozed__":
            return self.snoozed_in(account, offset, limit)
        if folder == "__muted__":
            return self.muted_in(account, offset, limit)
        if folder == "__waiting__":
            return self.waiting_in(account, offset, limit)
        if folder == "__replylater__":
            return self.replylater_in(account, offset, limit)
        scan: dict[str, bool] = self._folders.on_open(account, folder) if offset == 0 else {}
        c = self._store.conn()
        if group:
            # collapse to conversations: one row per thread (the latest message) + rollups
            rows = c.execute(
                """WITH fm AS (
                     SELECT m.*, s.importance, s.summary, s.action, s.category,
                       ROW_NUMBER() OVER (PARTITION BY m.thread_id ORDER BY m.ts DESC, m.pk DESC) rn,
                       COUNT(*)        OVER (PARTITION BY m.thread_id) cnt,
                       SUM(CASE WHEN m.seen=0 THEN 1 ELSE 0 END) OVER (PARTITION BY m.thread_id) unread,
                       MAX(m.flagged)  OVER (PARTITION BY m.thread_id) any_flagged,
                       MAX(m.has_attach) OVER (PARTITION BY m.thread_id) any_attach
                     FROM messages m LEFT JOIN summaries s ON s.message_pk=m.pk
                     WHERE m.account=? AND m.folder=? AND m.snoozed_until IS NULL AND m.pending_removal IS NULL """
                + _MUTED_EXCL.format(t="m") + """)
                   SELECT * FROM fm WHERE rn=1 ORDER BY ts DESC LIMIT ? OFFSET ?""",
                (account, folder, limit, offset)).fetchall()
            total = c.execute("SELECT COUNT(DISTINCT thread_id) FROM messages "
                              "WHERE account=? AND folder=? AND snoozed_until IS NULL AND pending_removal IS NULL "
                              + _MUTED_EXCL.format(t="messages"),
                              (account, folder)).fetchone()[0]
        else:
            rows = c.execute(
                """SELECT m.pk, m.message_key, m.from_addr, m.from_name, m.subject, m.date_iso, m.ts, m.snippet,
                          m.seen, m.flagged, m.has_attach, m.is_junk, m.bucket, m.thread_id, s.importance,
                          s.summary, s.action, s.category
                   FROM messages m LEFT JOIN summaries s ON s.message_pk=m.pk
                   WHERE m.account=? AND m.folder=? AND m.snoozed_until IS NULL AND m.pending_removal IS NULL """
                + _MUTED_EXCL.format(t="m") + """
                   ORDER BY m.ts DESC LIMIT ? OFFSET ?""",
                (account, folder, limit, offset)).fetchall()
            total = c.execute("SELECT COUNT(*) FROM messages "
                              "WHERE account=? AND folder=? AND snoozed_until IS NULL AND pending_removal IS NULL "
                              + _MUTED_EXCL.format(t="messages"),
                              (account, folder)).fetchone()[0]
        c.close()
        items: list[dict[str, Any]] = []
        for r in rows:
            it: dict[str, Any] = {
                "pk": r["pk"], "key": r["message_key"], "from_name": r["from_name"] or r["from_addr"],
                "from_addr": r["from_addr"],
                "subject": r["subject"] or "(no subject)", "when": rel_time(r["date_iso"]),
                "snippet": (r["snippet"] or "")[:200], "seen": bool(r["seen"]), "flagged": bool(r["flagged"]),
                "has_attach": bool(r["has_attach"]), "is_junk": bool(r["is_junk"]), "bucket": r["bucket"],
                "importance": r["importance"], **_attention(r), "thread_id": r["thread_id"],
                "summary": (r["summary"] or "")[:300], "action": r["action"] or "", "category": r["category"],
                "ts": r["ts"], "account": account,
            }
            if group:
                it.update({"count": r["cnt"], "seen": not r["unread"],
                           "flagged": bool(r["any_flagged"]), "has_attach": bool(r["any_attach"])})
            items.append(it)
        return {"ok": True, "account": account, "folder": folder, "items": items, "grouped": group,
                "total": total, "offset": offset, "has_more": offset + len(items) < total, **scan}


    def thread_json(self, account: str, thread_id: str) -> dict[str, Any]:
        """All messages in a conversation within an account, oldest->newest, de-duped by
        Message-ID (so a Gmail INBOX+All-Mail copy shows once)."""
        if not account or not thread_id:
            return {"ok": False, "error": "missing account/thread"}
        c = self._store.conn()
        rows = c.execute(
            """SELECT pk, message_key, folder, from_addr, from_name, subject, date_iso, ts, snippet,
                      seen, flagged, answered, has_attach, is_junk, message_id
               FROM messages WHERE account=? AND thread_id=? AND pending_removal IS NULL
               ORDER BY ts ASC, pk ASC""",
            (account, thread_id)).fetchall()
        c.close()
        items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for r in rows:
            key = (r["message_id"] or "").strip() or f"pk:{r['pk']}"
            if key in seen_ids:
                continue
            seen_ids.add(key)
            items.append({
                "pk": r["pk"], "key": r["message_key"], "folder": r["folder"],
            "from_name": r["from_name"] or r["from_addr"],
                "from_addr": r["from_addr"], "subject": r["subject"] or "(no subject)",
                "when": rel_time(r["date_iso"]), "snippet": (r["snippet"] or "")[:200],
                "seen": bool(r["seen"]), "flagged": bool(r["flagged"]), "answered": bool(r["answered"]),
                "has_attach": bool(r["has_attach"]), "is_junk": bool(r["is_junk"]),
            })
        reply_pk = items[-1]["pk"] if items else None
        return {"ok": True, "account": account, "thread_id": thread_id,
                "items": items, "count": len(items), "reply_pk": reply_pk}
