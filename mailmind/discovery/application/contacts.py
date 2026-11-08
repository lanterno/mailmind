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

    def facts(self, addr: str | None) -> dict[str, Any] | None:
        """What is said of a sender beside their name as a message opens: one read, so opening a message asks nothing more of
        the page. None for what can't be an address."""
        a = contacts.address(addr)
        return None if a is None else contacts.facts(self._mail.tally(a), ago)

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
        """Which of these addresses you've exchanged mail with, and for one you haven't, `near` if it reads as a slip of
        someone you've written to. The answer carries no subjects, counts or dates. `thin` means too little history to call
        anyone new."""
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
        role = self._folders.role_by_name
        items = known.verdicts(question, yours, hit)
        if not all(v["known"] for v in items.values()):       # only someone new can be a slip of someone else
            copies = self._mail.written_copies(yours, {folder for _, folder in known.sent_folders(facts, role)})
            known.mark_near_misses(question, items, known.written_to(facts, copies, yours, role))
        return {"ok": True, "items": items, "bad": question.bad, "thin": facts.thin}
