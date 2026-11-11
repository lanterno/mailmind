"""The ghost pruner's turn at one account (mailmind.mailbox.application.reconcile.Reconciler._auto_reconcile_one), over the
store in a temp database and a stand-in server behind the pool: a message gone from the server goes from the store, the
reconcile trail in the database says so, and an open list hears it. Each turn's record is committed before the next asks the
server anything. A user op taking the gate between two folders leaves the rest for the next turn. A reconcile someone asked
for (do_reconcile) keeps a record for each account it pruned."""
import contextlib
import json

from mailmind.bootstrap.mailbox import store
from mailmind.mailbox.adapters import reconcile_trail
from mailmind.mailbox.application.reconcile import Reconciler
from mailmind.mailbox.application.session import Selected
from mailmind.platform import sse
from mailmind.platform.db import txguard
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
        txguard.refuse_network("IMAP SEARCH")             # as a real session's every command does
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


class _Accounts:
    def __init__(self, *emails):
        self.emails = emails

    def preload_creds(self):
        return 0

    def accounts(self):
        return [{"email": e} for e in self.emails]

    def get(self, email):
        return {"email": email}


def reconciler(server, accounts=None):
    return Reconciler(UnitOfWork(), store, accounts, server, _Told(), _Told(), reconcile_trail)


def heard(monkeypatch):
    events = []
    monkeypatch.setattr(sse, "publish", lambda topic, data=None: events.append((topic, data)))
    return events


def audit():
    with UnitOfWork().read() as c:
        return [json.loads(r["body"]) for r in c.execute("SELECT body FROM audit_events WHERE trail='reconcile' ORDER BY id")]


def test_a_message_gone_from_the_server_goes_from_the_store_and_is_said(data_dir, monkeypatch):
    store_folder("INBOX", 1, 2, 3)
    events = heard(monkeypatch)
    reconciler(_Server(INBOX=(1, 3)))._auto_reconcile_one({"email": ADA})       # deleted in another client
    assert stored() == [1, 3]
    assert events == [("reconcile", {"account": ADA, "pruned": 1})]
    [record] = audit()
    assert record == {"kind": "reconcile", "ts": record["ts"], "auto": True, "account": ADA, "pruned": 1, "live": 2}
    assert list(record) == ["kind", "ts", "auto", "account", "pruned", "live"]           # as a line of the old trail held it


def test_each_turns_record_is_committed_before_the_next_asks_the_server(data_dir, monkeypatch):
    store_folder("INBOX", 1, 2, 3)
    heard(monkeypatch)
    server = _Server(INBOX=(1, 2))
    r = reconciler(server)
    r._auto_reconcile_one({"email": ADA})
    server.folders["INBOX"].discard(2)
    r._auto_reconcile_one({"email": ADA})                                       # its SEARCH would refuse an open write
    assert stored() == [1] and [a["pruned"] for a in audit()] == [1, 1]


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


def test_a_reconcile_asked_for_keeps_a_record_for_each_account_it_pruned(data_dir, monkeypatch):
    store_folder("INBOX", 1, 2, 3)
    out = reconciler(_Server(INBOX=(1,)), _Accounts(ADA)).do_reconcile("all", dry_run=False)
    assert (out["total_pruned"], stored()) == (2, [1])
    [record] = audit()
    assert record == {"kind": "reconcile", "ts": record["ts"], "account": ADA, "pruned": 2, "live": 1}
    assert list(record) == ["kind", "ts", "account", "pruned", "live"]


def test_a_dry_run_or_a_reconcile_that_pruned_nothing_keeps_no_record_nor_any_file(data_dir):
    store_folder("INBOX", 1, 2, 3)
    reconciler(_Server(INBOX=(1,)), _Accounts(ADA)).do_reconcile(ADA, dry_run=True)
    reconciler(_Server(INBOX=(1, 2, 3)), _Accounts(ADA)).do_reconcile(ADA, dry_run=False)
    assert audit() == [] and stored() == [1, 2, 3]
    assert [p.name for p in data_dir.iterdir() if not p.name.startswith("mailmind.db")] == []
