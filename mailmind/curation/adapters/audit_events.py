"""Curation's audit trail, the sweep trail, among the audit trails in the database (mailmind.platform.db.audit): a record of
each message a sweep or expiry trashes and of each sender's cleanup. It reads both trails as one, for the page. Each call
is a write of its own, committed when it returns."""
from mailmind.kernel.when import now_iso
from mailmind.platform.db import audit
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger

log = get_logger("cleanup")
_uow = UnitOfWork()


class AuditEvents:
    def swept(self, account, rows):
        """A record of each message a sweep is about to trash, all of one moment. One that can't be written raises, so
        nothing moves without its record."""
        stamp = now_iso()
        records = [{"kind": "sweep", "ts": stamp, "account": account, "uid": r["uid"], "from": r["from"],
                    "subject": r["subject"][:140], "bucket": r["bucket"], "reason": r["reason"]} for r in rows]
        if not records:
            return
        with _uow.write() as c:
            audit.append(c, audit.SWEEP, records)

    def cleaned_up(self, from_addr, token, swept, status):
        """A record of a sender's cleanup, once it is done. One that can't be written stops nothing."""
        try:
            with _uow.write() as c:
                audit.append(c, audit.SWEEP, [{"kind": "sender_cleanup", "ts": now_iso(), "from": from_addr,
                                               "token": token, "swept": swept, "status": status}])
        except Exception as e:
            log.warning("a cleanup's record wasn't written", exc_info=e)

    def read(self, limit=120):
        with _uow.read() as c:
            return audit.read(c, limit)
