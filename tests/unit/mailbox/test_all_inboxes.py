"""Every account's Inbox as one list (mailmind.mailbox.application.queries.Queries.all_inboxes), over the store in a temp
database and a stand-in for the sync: it answers from what is stored, so an account still on its first scan holds up no one,
and only the first page starts a scan. What the list holds and leaves out, through the app, is the integration half's."""
import threading

from mailmind.bootstrap.mailbox import store
from mailmind.mailbox.application.folders import Folders
from mailmind.mailbox.application.queries import Queries
from mailmind.platform.db.uow import UnitOfWork
from waiting import wait_until

ADA, BEA = "ada@example.test", "bea@example.test"


class _Accounts:
    def accounts(self):
        return [{"email": ADA}, {"email": BEA}]

    def get(self, email):
        return {"email": email}

    def error_kind(self, e):
        return type(e).__name__


class _Ingest:
    """Ada's Inbox read whole; Bea's first scan, once it starts, goes nowhere until `release` is set."""

    def __init__(self):
        self.release = threading.Event()
        self.started = []

    def folder_synced(self, account, folder):
        return account == ADA

    def sync_folder(self, acct, folder, full=False):
        self.started.append((acct["email"], folder, full))
        self.release.wait(60)
        return 0, 0, 0


def an_inbox_row(account, uid):
    with UnitOfWork().write() as c:
        store.upsert_message(c, {"account": account, "kind": "imap", "folder": "INBOX", "uid": uid,
                                 "message_id": f"<{uid}@example.test>", "gmail_thread": None, "from_addr": "lin@example.test",
                                 "from_name": "Lin", "subject": f"Note {uid}", "date_iso": None, "ts": uid, "snippet": "",
                                 "bucket": "personal/protected", "is_junk": 0})


def inboxes(ingest):
    folders = Folders(UnitOfWork(), store, _Accounts(), None, ingest, None)
    folders.FIRST_SYNC_WAIT = 3600                    # a list that waited for a first scan would never answer
    return Queries(UnitOfWork(), store, None, folders, _Accounts())


def scans_end(ingest):
    ingest.release.set()
    for t in threading.enumerate():
        if t.name == "folder-sync":
            t.join(30)


def test_an_account_on_its_first_scan_holds_up_no_one(data_dir):
    an_inbox_row(ADA, 1)
    an_inbox_row(BEA, 2)
    ingest, answered = _Ingest(), []
    lister = threading.Thread(target=lambda: answered.append(inboxes(ingest).all_inboxes(0, 500)), daemon=True)
    try:
        lister.start()
        assert wait_until(lambda: answered)
        [got] = answered
        assert got["syncing"] is True
        assert {i["account"] for i in got["items"]} == {ADA, BEA}         # Bea's as they are stored
        assert ingest.started == [(BEA, "INBOX", True)]
    finally:
        scans_end(ingest)


def test_a_later_page_starts_no_scan(data_dir):
    an_inbox_row(ADA, 1)
    an_inbox_row(BEA, 2)
    ingest = _Ingest()
    try:
        got = inboxes(ingest).all_inboxes(1, 1)
        assert "syncing" not in got and len(got["items"]) == 1
        assert ingest.started == []
    finally:
        scans_end(ingest)
