"""What reconcile may prune (mailmind.mailbox.application.reconcile.Reconciler.reconcile_account), over the store in a temp
database and a stand-in server behind the pool. What the server lost goes, and each answer that can't be trusted prunes
nothing: a wrong prune deletes real mail from the local view, and incremental sync, which only looks above the watermark,
never brings it back. The sync's own bookkeeping, and a prune through the fake server, are the integration half's."""
import contextlib
import imaplib

import pytest

from mailmind.bootstrap.mailbox import store
from mailmind.mailbox.application.reconcile import Reconciler
from mailmind.mailbox.application.session import Selected
from mailmind.platform.db.uow import UnitOfWork

ADA = "ada@example.test"
UIDVALIDITY = 1_700_000_001
STORED = (1, 2, 3, 4)


class _Server:
    """Ada's INBOX as the server holds it, by UID. `fault` makes a call raise; `after_search` runs once the search has
    answered, as a sync storing what arrived just then would."""

    def __init__(self, uids=STORED, uidvalidity=UIDVALIDITY):
        self.uids_held = set(uids)
        self.uidvalidity = uidvalidity
        self.fault = None
        self.after_search = None

    @contextlib.contextmanager
    def borrow(self, acct, timeout=None):
        yield self

    def _raise(self, call):
        if self.fault and self.fault[0] == call:
            raise self.fault[1]

    def select(self, folder, readonly=True):
        self._raise("select")
        return Selected(len(self.uids_held), self.uidvalidity, None)

    def uids(self, *criteria):
        self._raise("search")
        live = sorted(self.uids_held)
        if self.after_search:
            self.after_search()
        return live


def store_inbox(*uids, uidvalidity=UIDVALIDITY):
    """Ada's INBOX as a sync stored it, a row for each UID."""
    with UnitOfWork().write() as c:
        store.set_uidvalidity(c, ADA, "INBOX", uidvalidity)
        for uid in uids:
            store.upsert_message(c, {"account": ADA, "kind": "imap", "folder": "INBOX", "uid": uid,
                                     "message_id": f"<m{uid}@example.test>", "gmail_thread": None,
                                     "from_addr": "lin@example.test", "from_name": "Lin", "subject": f"Note {uid}",
                                     "date_iso": None, "ts": uid, "snippet": "", "bucket": "personal/protected", "is_junk": 0})


def inbox():
    with UnitOfWork().read() as c:
        return sorted((r["uid"], r["verified"]) for r in c.execute("SELECT uid, verified FROM messages WHERE account=? AND "
                                                                   "folder='INBOX'", (ADA,)))


def stored():
    return [uid for uid, _ in inbox()]


def reconcile(server, **kw):
    reconciler = Reconciler(UnitOfWork(), store, None, server, None, None, None)
    return reconciler.reconcile_account({"email": ADA}, **kw)


def counts(res):
    return {k: res[k] for k in ("stored", "live", "ghosts", "pruned", "error")}


def test_reconcile_prunes_what_the_server_lost_and_stamps_the_rest(data_dir):
    store_inbox(*STORED)
    res = reconcile(_Server(uids=(1, 2, 4)))
    assert counts(res) == {"stored": 4, "live": 3, "ghosts": 1, "pruned": 1, "error": None}
    assert inbox() == [(1, 1), (2, 1), (4, 1)]


def test_a_dry_run_counts_the_ghosts_and_changes_nothing(data_dir):
    store_inbox(*STORED)
    before = inbox()
    res = reconcile(_Server(uids=(1, 2, 4)), dry_run=True)
    assert (res["ghosts"], res["pruned"]) == (1, 0)
    assert inbox() == before


def test_reconcile_never_lowers_the_watermark(data_dir):
    store_inbox(*STORED, 5)
    with UnitOfWork().write() as c:
        store.set_watermark(c, ADA, 5, "2026-11-02T12:00:00+00:00", "INBOX")
    assert reconcile(_Server(uids=STORED))["pruned"] == 1          # the newest went elsewhere
    with UnitOfWork().read() as c:
        assert store.get_watermark(c, ADA, "INBOX") == 5


@pytest.mark.parametrize("fault", [("search", imaplib.IMAP4.error("SEARCH refused")),
                                   ("search", imaplib.IMAP4.abort("connection dropped")),
                                   ("select", imaplib.IMAP4.error("EXAMINE refused"))],
                         ids=["a refused SEARCH", "a dropped connection", "a refused EXAMINE"])
def test_reconcile_never_prunes_when_the_server_misbehaves(data_dir, fault):
    store_inbox(*STORED)
    before = inbox()
    server = _Server(uids=(1, 2, 4))
    server.fault = fault
    res = reconcile(server)
    assert res["error"] == type(fault[1]).__name__ and res["pruned"] == 0      # what the server said stays out of it
    assert inbox() == before


def test_reconcile_never_prunes_across_a_uidvalidity_change(data_dir):
    store_inbox(*STORED)
    before = inbox()
    res = reconcile(_Server(uids=(1, 2), uidvalidity=UIDVALIDITY + 1))
    assert "UIDVALIDITY changed" in res["error"] and res["pruned"] == 0
    assert inbox() == before


def test_an_empty_search_against_a_stored_inbox_prunes_only_when_forced(data_dir):
    store_inbox(*STORED)
    before = inbox()
    res = reconcile(_Server(uids=()))
    assert res["error"].startswith("empty SEARCH") and res["pruned"] == 0
    assert inbox() == before
    assert reconcile(_Server(uids=()), force=True)["pruned"] == len(STORED)
    assert inbox() == []


def test_mail_a_sync_stores_while_reconcile_runs_survives_it(data_dir):
    """The 300 s sync passes no IMAP gate: what it stores after the SEARCH answered isn't in that answer, and isn't gone."""
    store_inbox(*STORED)
    server = _Server(uids=STORED)
    server.after_search = lambda: store_inbox(5)
    assert reconcile(server)["pruned"] == 0
    assert stored() == [1, 2, 3, 4, 5]
