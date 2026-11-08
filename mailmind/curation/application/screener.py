"""The Screener, and a person's verdict on a sender."""
from collections.abc import Mapping
from typing import Any

from mailmind.curation.application.ports import FilingCommands, Mailboxes, RuleBookRepository, SenderMail
from mailmind.curation.domain import rulebook
from mailmind.kernel.errors import Refused
from mailmind.kernel.when import rel_time

SHOWN = 100         # the most the queue lists, of however many are left to decide on


class Screener:
    def __init__(self, rules: RuleBookRepository, mail: SenderMail, boxes: Mailboxes, filing: FilingCommands):
        self._rules, self._mail, self._boxes, self._filing = rules, mail, boxes, filing

    def queue(self, account: str | None) -> dict[str, Any]:
        """First-contact gate: distinct INBOX senders NOT yet decided (no keep/block rule covering
        their address or domain). One human decision each → Allow (keep) or Screen-out (block)."""
        if not account or account not in self._boxes.known():
            return {"ok": True, "account": account, "items": [], "total": 0}
        items = [{"from_addr": row["addr"], "from_name": row["name"] or row["addr"], "domain": rulebook.domain_of(row["addr"]),
                  "count": row["n"], "when": rel_time(row["iso"])}
                 for row in rulebook.undecided(self._mail.inbox_senders(account), self._rules.sets())]
        return {"ok": True, "account": account, "items": items[:SHOWN], "total": len(items)}

    def rule(self, b: Mapping[str, Any]) -> dict[str, Any]:
        """A keep, block or remove from the person, on a sender or a domain. Raises Refused for one that names neither, or
        no verdict it knows."""
        token = rulebook.token_of(b.get("token") or "")
        verdict = b.get("verdict")
        pk = b.get("pk")
        if not rulebook.is_token(token):
            raise Refused("invalid token (need a domain or an address)")
        if verdict == "remove":
            self._rules.remove(token)
            placed = None
        elif verdict in rulebook.VERDICTS:
            _, placed = self._rules.add(token, verdict)
        else:
            raise Refused("verdict must be keep|block|remove")
        trashed = False
        if verdict == "block" and pk is not None:  # close the loop: block also trashes now
            try:                                   # serialized with every other live-IMAP op
                trashed = self._filing.trash_now(int(pk))   # block-sender is NOT undoable
            except Exception:
                trashed = False
        reclassified = self._filing.reclassify(token)
        self._filing.stats_changed()
        return {"ok": True, "token": token, "placed_in": placed, "reclassified": reclassified,
                "trashed": trashed, "rules": self._rules.load()}
