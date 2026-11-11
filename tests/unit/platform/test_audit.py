"""The audit trails' table (mailmind.platform.audit): a record goes in its caller's transaction, and only into a trail there is."""
import pytest

from mailmind.platform import audit
from mailmind.platform.db.uow import UnitOfWork

RECORD = {"kind": "reconcile", "ts": "2026-11-02T12:00:00+00:00", "account": "ada@example.test", "pruned": 1, "live": 9}


def kept():
    with UnitOfWork().read() as c:
        return [tuple(r) for r in c.execute("SELECT trail, ts, body FROM audit_events")]


def test_a_record_goes_in_its_callers_transaction(data_dir):
    class Undone(Exception):
        pass
    with pytest.raises(Undone), UnitOfWork().write() as c:
        audit.append(c, audit.RECONCILE, [RECORD])
        raise Undone()
    assert kept() == []
    with UnitOfWork().write() as c:
        audit.append(c, audit.RECONCILE, [RECORD])
    assert kept() == [("reconcile", RECORD["ts"], '{"kind": "reconcile", "ts": "2026-11-02T12:00:00+00:00", '
                                                  '"account": "ada@example.test", "pruned": 1, "live": 9}')]


def test_a_trail_there_isnt_is_refused(data_dir):
    with pytest.raises(ValueError), UnitOfWork().write() as c:
        audit.append(c, "sweep_audit.jsonl", [RECORD])
    assert kept() == []
