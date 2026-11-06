"""The Screener, and a person's verdict on a sender."""
from mailmind.curation.application.ports import FilingCommands, Mailboxes, RuleBookRepository, SenderMail
from mailmind.curation.domain import rulebook
from mailmind.kernel.when import rel_time


class Screener:
    def __init__(self, rules: RuleBookRepository, mail: SenderMail, boxes: Mailboxes, filing: FilingCommands):
        self._rules, self._mail, self._boxes, self._filing = rules, mail, boxes, filing

    def queue(self, account):
        """First-contact gate: distinct INBOX senders NOT yet decided (no keep/block rule covering
        their address or domain). One human decision each → Allow (keep) or Screen-out (block)."""
        if not account or account not in self._boxes.known():
            return {"ok": True, "account": account, "items": [], "total": 0}
        r = self._rules.sets()
        items = []
        for row in self._mail.inbox_senders(account):
            addr = row["addr"]
            dom = addr.rpartition("@")[2]
            if rulebook.decided(addr, dom, r):   # kept or blocked, sub-domains too
                continue
            items.append({"from_addr": addr, "from_name": row["name"] or addr, "domain": dom,
                          "count": row["n"], "when": rel_time(row["iso"])})
        return {"ok": True, "account": account, "items": items[:100], "total": len(items)}

    def rule(self, b):
        """A keep, block or remove from the person, on a sender or a domain."""
        token = (b.get("token") or "").strip().lower()
        verdict = b.get("verdict")
        pk = b.get("pk")
        if not token or ("." not in token and "@" not in token):
            return {"ok": False, "msg": "invalid token (need a domain or an address)"}
        if verdict == "remove":
            self._rules.remove(token)
            placed = None
        elif verdict in ("keep", "block"):
            _, placed = self._rules.add(token, verdict)
        else:
            return {"ok": False, "msg": "verdict must be keep|block|remove"}
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
