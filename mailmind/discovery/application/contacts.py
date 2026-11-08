"""A person as your mail knows them: autocomplete, the contact card and the composer's "new to you" marks. Pure local
SQL over mail already stored: no enrichment, no IMAP, no IMAP gate, and nothing leaves 127.0.0.1."""
from collections.abc import Iterable
from typing import Any

from mailmind.discovery.application.ports import AccountNames, ContactMail, FolderRoles, KeptSenders
from mailmind.discovery.domain import contacts, known
from mailmind.kernel.errors import Refused
from mailmind.kernel.when import ago, rel_time


class Contacts:
    def __init__(self, mail: ContactMail):
        self._mail = mail

    def autocomplete(self, q: str | None) -> dict[str, Any]:
        """Everyone who has emailed you, ranked by frequency then recency."""
        rows = self._mail.senders_like(contacts.needle(q))
        return {"ok": True, "items": [contacts.suggestion(r) for r in rows]}

    def card(self, addr: str | None) -> dict[str, Any]:
        """One sender's card."""
        a = contacts.address(addr)
        if a is None:
            raise Refused("bad address")
        agg, accounts, recent = self._mail.card(a)
        return contacts.card(a, agg, accounts, recent, ago, rel_time)


class KnownContacts:
    def __init__(self, mail: ContactMail, accounts: AccountNames, folders: FolderRoles, rules: KeptSenders):
        self._mail = mail
        self._accounts = accounts
        self._folders = folders
        self._rules = rules

    def ask(self, addrs: object, own: Iterable[str] | None = None,
            min_history: int = known.MIN_HISTORY) -> dict[str, Any]:
        """Which of these addresses you've exchanged mail with. The answer carries no subjects, counts or dates.
        `thin` means too little history to call anyone new."""
        try:
            question = known.asked(addrs)
        except ValueError as e:               # our own words: they hold nothing typed
            raise Refused(str(e)) from e
        facts = self._mail.known_facts(list(question.want), min_history)
        yours = ({o.lower() for o in own} if own is not None
                 else known.own_addresses(self._accounts.emails(), self._accounts.aliases(), facts.accounts))
        yours |= known.sent_from(facts, self._folders.role_by_name)
        wanted = set(question.want)
        hit = known.vouched(facts, wanted, yours, self._folders.role_by_name)
        hit |= self._rules.kept() & wanted
        return {"ok": True, "items": known.verdicts(question, yours, hit), "bad": question.bad, "thin": facts.thin}
