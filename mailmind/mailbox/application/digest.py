"""What `/api/digest` answers: the items the brief is built from, and with them what the page needs of the rest of the app
as it opens, the identities to send from, the sync's state and the AI's."""
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from mailmind.kernel import attention as verdict
from mailmind.kernel.row import Row
from mailmind.kernel.when import now_iso, rel_time
from mailmind.mailbox.application.ports import Directory, StoredMail
from mailmind.mailbox.application.syncing import Syncing
from mailmind.platform import profile


def _attention(r: Row) -> dict[str, bool]:
    """What the verdict on row `r` asks of you. The page reads these and never works them out."""
    return verdict.answers(r["importance"], r["category"], r["action"])


class Digest:
    def __init__(self, store: StoredMail, accounts: Directory, syncing: Syncing, ai_status: Callable[[], dict[str, Any]]):
        self._store = store
        self._accounts = accounts
        self._syncing = syncing
        self._ai_status = ai_status

    def answer(self, qs: Mapping[str, Sequence[str]]) -> dict[str, Any]:
        min_imp = int(qs.get("min", ["1"])[0])
        account = qs.get("account", [None])[0] or None
        c = self._store.conn()
        rows = self._store.digest(c, min_importance=min_imp, account=account, limit=400)
        keys: set[str] = set(rows[0].keys()) if rows else set()
        amap = self._accounts.alias_map()
        wmap = self._accounts.webmail_map()
        items = [{
            "pk": r["pk"], "key": r["message_key"], "account": r["account"], "kind": r["kind"],
            "alias": amap.get(r["account"]),
            "from_name": r["from_name"] or r["from_addr"], "from_addr": r["from_addr"],
            "subject": r["subject"] or "(no subject)", "when": rel_time(r["date_iso"]),
            "summary": r["summary"], "importance": r["importance"], **_attention(r),
            "category": r["category"], "action": r["action"] or "", "link": self._accounts.deep_link(r, wmap),
            "bucket": (r["bucket"] if "bucket" in keys else None),
            "confidence": (r["confidence"] if "confidence" in keys else None),
            "reason": (r["reason"] if "reason" in keys else None),
            "verified": (bool(r["verified"]) if "verified" in keys else False),
            "last_seen": rel_time(r["last_seen"]) if "last_seen" in keys and r["last_seen"] else None,
            # what a Mailbox row needs to draw a digest item like a folder item (Needs you, briefing topics)
            "seen": bool(r["seen"]), "flagged": bool(r["flagged"]), "has_attach": bool(r["has_attach"]),
            "thread_id": r["thread_id"], "folder": r["folder"], "ts": r["ts"],
        } for r in rows]
        accts = [row[0] for row in c.execute("SELECT DISTINCT account FROM messages ORDER BY account").fetchall()]
        st = self._store.stats(c)
        c.close()
        # send_accounts = the authoritative configured (enabled) identities — the compose
        # From list + reply-all self-dedupe set, independent of what's been synced yet.
        send_accounts = [a["email"] for a in self._accounts.accounts()]
        return {"items": items, "stats": st, "accounts": accts, "send_accounts": send_accounts,
                "aliases": amap, "signatures": self._accounts.signature_map(),
                "signature_default": self._accounts.signature_default(),
                "status": self._syncing.status, "sync": self._syncing.sync, "ai": self._ai_status(), "profile": profile.current().value,
                "generated": now_iso()}
