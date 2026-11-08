"""What `/api/digest` answers: the items the brief is built from, and with them what the page needs of the rest of the app
as it opens, the identities to send from, the sync's state and the AI's."""
from collections.abc import Callable
from typing import Any

from mailmind.kernel import attention as verdict
from mailmind.kernel.row import Row
from mailmind.kernel.when import now_iso, rel_time
from mailmind.mailbox.application.ports import Directory, StoredMail
from mailmind.mailbox.application.syncing import Syncing
from mailmind.platform import profile
from mailmind.platform.db.uow import UnitOfWork


def _attention(r: Row) -> dict[str, bool]:
    """What the verdict on row `r` asks of you. The page reads these and never works them out."""
    return verdict.answers(r["importance"], r["category"], r["action"])


class Digest:
    def __init__(self, uow: UnitOfWork, store: StoredMail, accounts: Directory, syncing: Syncing, ai_status: Callable[[], dict[str, Any]]):
        self._uow = uow
        self._store = store
        self._accounts = accounts
        self._syncing = syncing
        self._ai_status = ai_status

    def answer(self) -> dict[str, Any]:
        with self._uow.read() as c:
            rows = self._store.digest(c, limit=400)
            amap = self._accounts.alias_map()
            wmap = self._accounts.webmail_map()
            items = [{
                "key": r["message_key"], "uid": r["uid"], "account": r["account"], "kind": r["kind"],
                "alias": amap.get(r["account"]),
                "from_name": r["from_name"] or r["from_addr"], "from_addr": r["from_addr"],
                "subject": r["subject"] or "(no subject)", "when": rel_time(r["date_iso"]),
                "summary": r["summary"], "importance": r["importance"], **_attention(r),
                "category": r["category"], "action": r["action"] or "", "link": self._accounts.deep_link(r, wmap),
                "bucket": r["bucket"],
                # what a Mailbox row needs to draw a digest item like a folder item (Needs you, briefing topics)
                "seen": bool(r["seen"]), "flagged": bool(r["flagged"]), "has_attach": bool(r["has_attach"]),
                "thread_id": r["thread_id"], "folder": r["folder"], "ts": r["ts"],
            } for r in rows]
            accts = self._store.stored_accounts(c)
            st = self._store.stats(c)
        # send_accounts = the authoritative configured (enabled) identities — the compose
        # From list + reply-all self-dedupe set, independent of what's been synced yet.
        send_accounts = [a["email"] for a in self._accounts.accounts()]
        return {"items": items, "stats": st, "accounts": accts, "send_accounts": send_accounts,
                "aliases": amap, "signatures": self._accounts.signature_map(),
                "signature_default": self._accounts.signature_default(),
                "status": self._syncing.status, "sync": self._syncing.sync, "ai": self._ai_status(), "profile": profile.current().value,
                "generated": now_iso()}
