"""A person as your mail knows them: autocomplete, the contact card and the composer's "new to you" marks. Pure local
SQL over mail already stored: no enrichment, no IMAP, no IMAP gate, and nothing leaves 127.0.0.1."""
from collections.abc import Iterable
from typing import Any

from mailmind.discovery.application.ports import AccountNames, ContactMail, FolderRoles, KeptSenders
from mailmind.discovery.domain import known
from mailmind.kernel.parsed import is_list
from mailmind.kernel.when import ago, rel_time
from mailmind.kernel import addresses


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

    def _normal(self, raw: object) -> str | None:
        """The address mail to `raw` would actually go to, by compose's own parser, so a display
        name holding someone else's address can't stand in for it. None unless exactly one."""
        try:
            pairs = addresses.parse_addr_list([str(raw or "")[:320]])
        except ValueError:
            return None
        if len(pairs) != 1:
            return None
        a = pairs[0][1].lower()
        return a if len(a) <= 254 else None

    def _forms(self, a: str) -> set[str]:
        """a plus its IDNA twin: the outbox envelope stores punycode domains, received mail may not."""
        local, dom = a.split("@")
        out = {a}
        try:
            out.add(f"{local}@{addresses.idna_domain(dom).lower()}")
        except ValueError:
            pass
        if "xn--" in dom:
            try:
                out.add(f"{local}@{dom.encode('ascii').decode('idna').lower()}")
            except UnicodeError:
                pass
        return out

    def _own(self, holding: Iterable[str]) -> set[str]:
        """Your addresses, as the dashboard's state.myAddrs sees them."""
        own = {e.lower() for e in self._accounts.emails()}
        own |= {e.lower() for e in self._accounts.aliases()}
        own |= {a.lower() for a in holding}
        return own

    def ask(self, addrs: object, own: Iterable[str] | None = None,
            min_history: int = known.MIN_HISTORY) -> tuple[dict[str, Any], int]:
        """Which of these addresses you've exchanged mail with. The answer carries no subjects, counts or dates.
        `thin` means too little history to call anyone new. (result, code), like the contact card."""
        if not is_list(addrs):
            return {"ok": False, "error": "addrs must be a list"}, 400
        if len(addrs) > addresses.MAX_RECIPIENTS:
            return {"ok": False, "error": "too many addresses"}, 400
        items: dict[str, dict[str, bool]] = {}
        bad: list[str] = []
        want: dict[str, set[str]] = {}
        for raw in addrs:
            key = str(raw or "").strip().lower()[:320]
            a = self._normal(raw)
            if a is None:
                bad.append(key[:200])
                continue
            items[key] = {"known": False, "self": False}
            for f in self._forms(a):
                want.setdefault(f, set()).add(key)
        facts = self._mail.known_facts(list(want), min_history)
        own = {o.lower() for o in own} if own is not None else self._own(facts.accounts)
        wanted = set(want)
        self_hit = wanted & own
        hit = known.vouched(facts, wanted, own, self._folders.role_by_name)
        hit |= self._rules.kept() & wanted
        for f, keys in want.items():
            for k in keys:
                if f in self_hit:
                    items[k]["self"] = items[k]["known"] = True
                elif f in hit:
                    items[k]["known"] = True
        return {"ok": True, "items": items, "bad": bad, "thin": facts.thin}, 200
