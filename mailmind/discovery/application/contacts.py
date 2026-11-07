"""A person as your mail knows them: autocomplete, the contact card and the composer's "new to you" marks. Pure local
SQL over mail already stored: no enrichment, no IMAP, no IMAP gate, and nothing leaves 127.0.0.1."""
from collections.abc import Iterable
from typing import Any

from mailmind.discovery.application.ports import AccountNames, ContactMail, FolderRoles, KeptSenders
from mailmind.discovery.domain import known
from mailmind.kernel.when import ago, rel_time


class Contacts:
    def __init__(self, mail: ContactMail):
        self._mail = mail

    def autocomplete(self, q: str | None) -> dict[str, Any]:
        """Everyone who has emailed you, ranked by frequency then recency."""
        q = (q or "").strip().lower()[:64]
        rows = self._mail.senders_like(q)
        return {"ok": True, "items": [{"addr": r["addr"], "name": r["name"] or "", "n": r["n"]} for r in rows]}

    def card(self, addr: str | None) -> tuple[dict[str, Any], int]:
        """Relationship memory for one sender: interaction stats, which accounts they write to and recent threads.
        (result, code)."""
        a = (addr or "").strip().lower()
        if not a or "@" not in a:
            return {"ok": False, "error": "bad address"}, 400
        agg, accts, recent = self._mail.card(a)
        n = agg["n"] or 0
        return {"ok": True, "addr": a, "name": (agg["name"] or ""), "count": n,
                "replied": agg["replied"] or 0, "known": n > 0,
                "last_ago": (ago(agg["last"]) if agg["last"] else None),
                "first_ago": (ago(agg["first"]) if agg["first"] else None),
                "accounts": accts,                         # raw emails; client renders via nameOf()
                "recent": [{"pk": r["pk"], "account": r["account"], "subject": r["subject"] or "(no subject)",
                            "thread_id": r["thread_id"], "when": rel_time(r["date_iso"]), "seen": bool(r["seen"])}
                           for r in recent]}, 200


class KnownContacts:
    def __init__(self, mail: ContactMail, accounts: AccountNames, folders: FolderRoles, rules: KeptSenders):
        self._mail = mail
        self._accounts = accounts
        self._folders = folders
        self._rules = rules

    def ask(self, addrs: object, own: Iterable[str] | None = None,
            min_history: int = known.MIN_HISTORY) -> tuple[dict[str, Any], int]:
        """Which of these addresses you've exchanged mail with. The answer carries no subjects, counts or dates.
        `thin` means too little history to call anyone new. (result, code), like the contact card."""
        try:
            question = known.asked(addrs)
        except ValueError as e:
            return {"ok": False, "error": str(e)}, 400
        facts = self._mail.known_facts(list(question.want), min_history)
        yours = ({o.lower() for o in own} if own is not None
                 else known.own_addresses(self._accounts.emails(), self._accounts.aliases(), facts.accounts))
        wanted = set(question.want)
        hit = known.vouched(facts, wanted, yours, self._folders.role_by_name)
        hit |= self._rules.kept() & wanted
        return {"ok": True, "items": known.verdicts(question, yours, hit), "bad": question.bad, "thin": facts.thin}, 200
