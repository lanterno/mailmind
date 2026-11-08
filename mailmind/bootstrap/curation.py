"""Curation's wiring: the one place that binds its ports, to its own adapters and, until those contexts move, to
the legacy modules behind filing and sending. The legacy modules reach Curation through here.
"""
from mailmind.bootstrap import mailbox
from mailmind.curation.adapters import rules_file as rules
from mailmind.curation.adapters import safe_http, sqlite
from mailmind.curation.adapters.audit_file import AuditFile
from mailmind.curation.api import (BLOCKED, EXPIRE, JUNK_BUCKETS, KEYS, MKT, NOTIF, PERSONAL, PROTECTED, RECEIPT, SPAM,
                                   arrival_ts, by_rule, decided, domain_listed, expiry_hours, is_junk, looks_human,
                                   select_expired)
from mailmind.curation.application.cleanup import Cleanup
from mailmind.curation.application.expiry import Expiry
from mailmind.curation.application.filters import Filters
from mailmind.curation.application.screener import Screener
from mailmind.curation.application.sweep import Sweeps
from mailmind.curation.application.unsubscribe import Unsubscribing
from mailmind.curation.domain import classifier
from mailmind.mailbox.application import bulk_moves
from mailmind.platform import perimeter, sse
from mailmind.platform.logging import get_logger
from mailmind.platform.workers import gates

__all__ = ["AUDIT", "BLOCKED", "EXPIRE", "JUNK_BUCKETS", "KEYS", "MKT", "NOTIF", "PERSONAL", "PROTECTED", "RECEIPT",
           "SPAM", "arrival_ts", "by_rule", "classify", "cleanup", "decided", "domain_listed", "expiry",
           "expiry_hours", "filters", "is_junk", "looks_human", "rules", "safe_http", "screener", "select_expired", "sqlite",
           "sweeps"]

log = get_logger("unsub")
UNSUB_READ_WAIT = 5        # seconds a preview waits its turn on the account's gate to read one message's headers

def _csrf():
    """This run's secret, the page's token."""
    return perimeter.CSRF


def classify(from_addr, subject="", from_name="", has_list_unsub=False):
    """The classifier over the rule book as it stands."""
    return classifier.classify(from_addr, subject, from_name, has_list_unsub, rules=rules.sets())


class _MessageRows:
    """What only the store may do to a message's row, until the Mailbox holds them."""
    def set_list_unsub(self, c, pk, raw, method):
        from mailmind.bootstrap.mailbox import store
        store.set_list_unsub(c, pk, raw, method)

    def delete_message(self, c, pk):
        from mailmind.bootstrap.mailbox import store
        store.delete_message(c, pk)

    def delete_by_uids(self, c, account, folder, uids):
        from mailmind.bootstrap.mailbox import store
        store.delete_by_uids(c, account, folder, uids)


class _Conditions:
    def refusal(self, q):
        from mailmind.bootstrap import discovery
        return discovery.conditions.refusal(q)

    def holds(self, c, pk, q):
        from mailmind.bootstrap import discovery
        return discovery.conditions.holds(c, pk, q)


class _Filing:
    def trash(self, pk, undo_group):
        return mailbox.filing.do_trash({"pk": pk, "undo_group": undo_group})

    def trash_now(self, pk):
        return mailbox.filing.trash_now(pk)

    def trash_many(self, rows, undo_group):
        return mailbox.filing.file_many({"verb": "trash", "rows": list(rows), "undo_group": undo_group})

    def undo_push(self, descriptor, group):
        mailbox.undo.push(descriptor, group)

    def reclassify(self, token):
        from mailmind.bootstrap.mailbox import ingest
        return ingest.reclassify(token)

    def stats_changed(self):
        mailbox.stats.invalidate()

    def folders_changed(self, account, moved):
        mailbox.stats.invalidate()
        mailbox.folders.invalidate(account)
        try:                                              # same refresh as a ghost prune: open lists drop the row
            sse.publish("reconcile", {"account": account, "pruned": moved})
        except Exception:
            pass


class _SystemSend:
    def send(self, account, raw, message_id, to, subject, key):
        from mailmind.bootstrap import accounts
        from mailmind.bootstrap import compose
        accounts.get(account)                             # a removed/stale account raises KeyError
        compose.outbox.enqueue({"raw": raw, "message_id": message_id, "envelope": [to],
                                "to_disp": [to], "cc_disp": [], "bcc_disp": []},
                               {"account": account, "idempotency_key": key, "subject": subject})


class _SafeHttp:
    def one_click_post(self, url):
        return safe_http.one_click_post(url)


class _ListHeaders:
    def read(self, r):
        from mailmind.bootstrap import accounts
        acct = next((a for a in accounts.accounts() if a["email"] == r["account"]), None)
        if acct is None:
            return None

        def read():
            s = mailbox.open_session(acct)
            try:
                s.select(r["folder"])
                return s.headers([r["uid"]], ("LIST-UNSUBSCRIBE", "LIST-UNSUBSCRIBE-POST"))
            finally:
                s.logout()
        try:
            got = gates.run(r["account"], read, priority=gates.Priority.INTERACTIVE, deadline=UNSUB_READ_WAIT)
        except Exception as e:                              # a preview still works: it offers no unsubscribe
            log.warning("header read failed", exc_info=e)
            return None
        return got[0].raw if got else None


class _Mailboxes:
    def all(self):
        from mailmind.bootstrap import accounts
        return accounts.accounts()

    def get(self, email):
        from mailmind.bootstrap import accounts
        return accounts.get(email)

    def known(self):
        return {a["email"] for a in self.all()}

    def preload_creds(self):
        from mailmind.bootstrap import accounts
        accounts.preload_creds()

    def open_session(self, acct):
        return mailbox.open_session(acct)

    def borrow(self, acct):
        from mailmind.bootstrap.mailbox import pool
        return pool.borrow(acct)

    def trash_folder(self, acct):
        from mailmind.bootstrap import accounts
        return accounts.trash_folder(acct)

    def quote(self, value):
        from mailmind.bootstrap import accounts
        return accounts.imap_quote(value)

    def move_verified(self, session, uids, dest):
        return bulk_moves.move_verified(session, uids, dest)


AUDIT = AuditFile()
_mail = sqlite.SenderMail(_MessageRows())
_filing = _Filing()
_boxes = _Mailboxes()
cleanup = Cleanup(rules, _mail, Unsubscribing(_mail, _ListHeaders(), _SafeHttp(), _SystemSend()), _filing, AUDIT,
                  _csrf)
screener = Screener(rules, _mail, _boxes, _filing)
sweeps = Sweeps(rules, _mail, _boxes, AUDIT, _filing)
expiry = Expiry(rules, _mail, _boxes, AUDIT, _filing)
filters = Filters(rules, _Conditions())
