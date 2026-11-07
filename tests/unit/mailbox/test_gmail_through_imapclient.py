"""A Gmail account that asks for IMAPClient, through the app, which is how one soaks it before it becomes the default. The
world's accounts go through imaplib, as real ones do, so every other app test covers that; the contract tests hold each adapter
call by call below this."""
import pytest

from fixtures import world as w
from mailmind.bootstrap import accounts, mailbox

UNRELATED = "cy-inbox-03-unrelated.eml"
THREAD_END = "cy-inbox-02-thread-end.eml"
ALL_MAIL = "[Gmail]/All Mail"


@pytest.fixture(autouse=True)
def cy_asks(world):
    w.ask_for_imapclient(world.dir, w.CY)


def boxes(world, name):
    return sorted(box for _, box, _, _ in world.where(name)["server"])


def listed(app):
    return [it["subject"] for it in app.get(f"/api/messages?account={w.CY}").json["items"]]


def test_the_account_is_reached_through_imapclient(app):
    session = mailbox.open_session(accounts.get(w.CY), timeout=10)
    session.logout()
    assert type(session).__name__ == "ImapClientSession"


def test_a_sync_brings_in_new_mail(app, world):
    world.server.deliver(w.CY, "INBOX", w.variant(UNRELATED, Message_ID="<parcel-2@parcels.example.test>",
                                                  Subject="Your parcel was delivered"))
    assert app.post("/api/sync-retry-account", {"account": w.CY}).json["ok"]
    app.settle()
    assert "Your parcel was delivered" in listed(app)


def test_a_sync_reads_a_flag_changed_elsewhere(app, world):
    uid = world.uid(THREAD_END)
    assert world.row(THREAD_END)["seen"] == 0
    with world.server.lock:
        world.server.mailbox(w.CY, "INBOX").messages[uid].flags.add(w.SEEN)
    assert app.post("/api/sync-retry-account", {"account": w.CY}).json["ok"]
    app.settle()
    assert world.row(THREAD_END)["seen"] == 1


def test_archive_takes_only_the_inbox_label_off(app, world):
    uid, subject = world.uid(UNRELATED), world.row(UNRELATED)["subject"]
    assert boxes(world, UNRELATED) == ["INBOX", ALL_MAIL]
    mark = world.mark()
    r = app.post("/api/archive", {"pk": world.pk(UNRELATED)}).json
    assert r["ok"]
    assert world.writes(mark) == [f"{w.CY} [INBOX] UID STORE {uid} -X-GM-LABELS (\\Inbox)"]
    assert boxes(world, UNRELATED) == [ALL_MAIL]
    assert subject not in listed(app)


def test_undo_of_an_archive_puts_the_label_back(app, world):
    subject = world.row(UNRELATED)["subject"]
    app.post("/api/archive", {"pk": world.pk(UNRELATED)})
    mark = world.mark()
    r = app.post("/api/undo", {}).json
    assert (r["ok"], r["label"], r["remaining"]) == (True, "Archived", 0)
    [label] = [line for line in world.writes(mark) if "X-GM-LABELS" in line]
    assert label.startswith(f"{w.CY} [{ALL_MAIL}] UID STORE ") and label.endswith("+X-GM-LABELS (\\Inbox)")
    assert boxes(world, UNRELATED) == ["INBOX", ALL_MAIL]
    assert subject in listed(app)


def test_a_conversation_archived_together_comes_back_together(app, world):
    r = app.post("/api/thread/archive", {"pk": world.pk(THREAD_END), "undo_group": "the-venue-thread"}).json
    assert r["ok"]
    assert boxes(world, THREAD_END) == [ALL_MAIL]
    assert app.post("/api/undo", {}).json["ok"]
    assert boxes(world, THREAD_END) == ["INBOX", ALL_MAIL]
