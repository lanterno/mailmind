"""Shelf life: moving INBOX mail past its rule's age to Trash. Ages come from the live server, so it's right
whether or not the store is synced. Verified and audited, and the audit comes before the move."""
import email
import imaplib
import time
from typing import Any

from mailmind.content import mime
from mailmind.content.mime import dec
from mailmind.curation.application.ports import AuditLog, FilingCommands, Mailboxes, RuleBookRepository, SenderMail
from mailmind.curation.domain.expiry import arrival_ts, select_expired
from mailmind.curation.domain.live import Aged
from mailmind.curation.domain.rulebook import EXPIRE
from mailmind.platform.logging import get_logger
from mailmind.platform.workers import gates

log = get_logger("expire")
EXP_FIELDS = ("FROM", "SUBJECT", "MESSAGE-ID", "DATE")


class Expiry:
    def __init__(self, rules: RuleBookRepository, mail: SenderMail, boxes: Mailboxes, audit: AuditLog,
                 filing: FilingCommands):
        self._rules, self._mail, self._boxes, self._audit, self._filing = rules, mail, boxes, audit, filing

    def account(self, acct: dict[str, Any], dry: bool = False, now: float | None = None) -> dict[str, Any]:
        """Move INBOX mail past its expiry rule to Trash — verified, audited, and pruned from the local DB
        by Message-ID. Errors are captured, not raised."""
        expire = self._rules.load()[EXPIRE]
        out: dict[str, Any] = {"account": acct["email"], "planned": 0, "moved": 0, "stragglers": [], "error": None}
        if not expire:
            return out
        try:
            s = self._boxes.open_session(acct)
        except Exception as e:
            log.warning("an account failed", exc_info=e)
            out["error"] = type(e).__name__      # what a server said stays out of the answer
            return out
        try:                                    # single try/finally so s is ALWAYS logged out (no socket leak)
            s.select("INBOX")
            uids: set[int] = set()
            for token in expire:                # coarse server-side filter; select_expired matches exactly
                try:
                    uids.update(s.uids("FROM", self._boxes.quote(token)))
                except imaplib.IMAP4.abort:
                    raise
                except imaplib.IMAP4.error:     # a refused search finds nothing
                    pass
            rows: list[Aged] = []
            for h in s.headers(sorted(uids), EXP_FIELDS, internaldate=True):
                msg = email.message_from_bytes(h.raw)
                rows.append({"uid": str(h.uid), "from": mime.sender_of(msg)[1].lower(),
                             "subject": dec(msg.get("Subject", "")),
                             "mid": (msg.get("Message-ID", "") or "").strip(),
                             "ts": arrival_ts(h.internaldate, msg)})
            targets = select_expired(rows, expire, now if now is not None else time.time())
            out["planned"] = len(targets)
            if dry or not targets:
                return out
            self._audit.swept(acct["email"], [      # log BEFORE moving so a crash still records intent
                {"uid": t["uid"], "from": t["from"], "subject": t["subject"], "bucket": "expired",
                 "reason": f"expire rule: older than {t['hours']}h"} for t in targets])
            moved, stragglers = self._boxes.move_verified(s, [t["uid"] for t in targets],
                                                          self._boxes.trash_folder(acct))
        except Exception as e:
            log.warning("an account failed", exc_info=e)
            out["error"] = type(e).__name__      # what a server said stays out of the answer
            return out
        finally:
            try:
                s.logout()
            except Exception:
                pass
        out["moved"], out["stragglers"] = moved, stragglers
        self._mail.forget_expired(acct["email"], targets, stragglers)
        return out

    def auto_one(self, acct: dict[str, Any]) -> None:
        """One account's turn in the background job. It gives way to anyone using the account's gate."""
        try:
            res = gates.run(acct["email"], lambda: self.account(acct),
                            priority=gates.Priority.MAINTENANCE, wait=False)
        except gates.Busy:
            return                                        # user op live — yield, retry next cycle
        if res.get("error"):
            log.warning("%(account)s: %(why)s", {"account": acct["email"], "why": res["error"].partition(":")[0]})
            return
        if not res.get("moved"):
            return
        self._filing.folders_changed(acct["email"], res["moved"])
        log.info("%(account)s: moved %(count)s expired message(s) to Trash", {"account": acct["email"], "count": res["moved"]})

    def round(self) -> None:
        """One pass of the expiry rules over every account, when there are any rules."""
        if self._rules.load()[EXPIRE]:
            for a in self._boxes.all():
                try:
                    self.auto_one(a)
                except Exception as e:
                    log.warning("loop error", exc_info=e)
