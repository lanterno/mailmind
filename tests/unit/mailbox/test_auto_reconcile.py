"""The ghost pruner's turn at one account (mailmind.mailbox.application.reconcile.Reconciler._auto_reconcile_one), over the
store in a temp database and a stand-in server behind the pool: a message gone from the server goes from the store, the
audit trail says so, and an open list hears it. A user op taking the gate between two folders leaves the rest for the next
turn."""
import contextlib
import json
import os

from mailmind.bootstrap.mailbox import store
from mailmind.mailbox.application.reconcile import Reconciler
from mailmind.mailbox.application.session import Selected
from mailmind.platform import settings, sse
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.workers import gates

ADA = "ada@example.test"
UIDVALIDITY = 1_700_000_001


class _Server:
    """Ada's folders as the server holds them: folder -> UIDs."""

    def __init__(self, **folders):
        self.folders = {name.replace("_", " "): set(uids) for name, uids in folders.items()}
        self.selected = None

    @contextlib.contextmanager
    def borrow(self, acct, timeout=None):
        yield self

    def select(self, folder, readonly=True):
        self.selected = folder
        return Selected(len(self.folders[folder]), UIDVALIDITY, None)

    def uids(self, *criteria):
        return sorted(self.folders[self.selected])


class _Told:
    """The rail's badges and the counts, which a prune makes stale."""

    def __init__(self):
        self.told = []

    def invalidate(self, *account):
        self.told.append(account)


def store_folder(folder, *uids):
    with UnitOfWork().write() as c:
        store.set_uidvalidity(c, ADA, folder, UIDVALIDITY)
        for uid in uids:
            store.upsert_message(c, {"account": ADA, "kind": "imap", "folder": folder, "uid": uid,
                                     "message_id": f"<{folder}-{uid}@example.test>", "gmail_thread": None,
                                     "from_addr": "lin@example.test", "from_name": "Lin", "subject": f"Note {uid}",
                                     "date_iso": None, "ts": uid, "snippet": "", "bucket": "personal/protected", "is_junk": 0})


def stored(folder="INBOX"):
    with UnitOfWork().read() as c:
        return sorted(store.stored_uids(c, ADA, folder))


def reconciler(server):
    return Reconciler(UnitOfWork(), store, None, server, _Told(), _Told())


def heard(monkeypatch):
    events = []
    monkeypatch.setattr(sse, "publish", lambda topic, data=None: events.append((topic, data)))
    return events


def audit():
    try:
        return [json.loads(line) for line in open(settings.current().reconcile_audit).read().splitlines()]
    except FileNotFoundError:
        return []


def test_a_message_gone_from_the_server_goes_from_the_store_and_is_said(data_dir, monkeypatch):
    store_folder("INBOX", 1, 2, 3)
    events = heard(monkeypatch)
    reconciler(_Server(INBOX=(1, 3)))._auto_reconcile_one({"email": ADA})       # deleted in another client
    assert stored() == [1, 3]
    assert events == [("reconcile", {"account": ADA, "pruned": 1})]
    [line] = audit()
    assert {k: line[k] for k in ("kind", "auto", "account", "pruned")} == \
        {"kind": "reconcile", "auto": True, "account": ADA, "pruned": 1}
    assert oct(os.stat(settings.current().reconcile_audit).st_mode & 0o777) == "0o600"     # it names what was pruned


def test_with_nothing_gone_nothing_is_said(data_dir, monkeypatch):
    store_folder("INBOX", 1, 2, 3)
    events = heard(monkeypatch)
    reconciler(_Server(INBOX=(1, 2, 3)))._auto_reconcile_one({"email": ADA})
    assert events == [] and audit() == []


def test_a_list_that_cant_hear_it_stops_nothing(data_dir, monkeypatch):
    def deaf(topic, data=None):
        raise RuntimeError("no one is listening")
    monkeypatch.setattr(sse, "publish", deaf)
    store_folder("INBOX", 1, 2, 3)
    reconciler(_Server(INBOX=(1, 3)))._auto_reconcile_one({"email": ADA})
    assert stored() == [1, 3] and len(audit()) == 1


def test_a_user_op_between_two_folders_leaves_the_rest_for_the_next_turn(data_dir, monkeypatch):
    store_folder("INBOX", 1, 2)
    store_folder("Archive", 3, 4)
    store_folder("Sent", 5, 6)
    r = reconciler(_Server(INBOX=(1,), Archive=(3,), Sent=(5,)))
    assert r.reconciled_folders(ADA) == ["INBOX", "Archive", "Sent"]
    real, turns = gates.run, []

    def busy_after_the_first(account, fn, **kw):
        turns.append(account)
        if len(turns) > 1:
            raise gates.Busy()
        return real(account, fn, **kw)
    monkeypatch.setattr(gates, "run", busy_after_the_first)
    out = r._reconcile_folders({"email": ADA}, priority=gates.Priority.MAINTENANCE, wait=False)
    assert (out["folders"], out["pruned"], turns) == (1, 1, [ADA, ADA])
    assert (stored("INBOX"), stored("Archive"), stored("Sent")) == ([1], [3, 4], [5, 6])
