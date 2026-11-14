"""Warming folders beyond the Inbox over HTTP (`warm.warm_tick`, `work warm`): off unless asked for. An account asking
(`"warm_folders": true` in the file, while MAILMIND_WARM_FOLDERS is unset) has its Drafts synced every round, its archive
every second and its Sent every third; a Gmail account's All Mail holds its Inbox, so it isn't warmed. The variable, when
set, decides over the file. A sync round warms as it ends, after the Inbox.

Mail lands on the server here without a sync, so only warming brings it in.
"""
import json

import pytest

from fixtures import world as w
from mailmind.bootstrap.mailbox import store

LIKE = "ada-inbox-02-lunch-confirm.eml"


def asking(app, world, *emails):
    """These accounts' entries in the file ask to be warmed, written while the server is stopped."""
    path = world.dir / "mailmind.config.json"
    with app.stopped():
        written = json.loads(path.read_text())
        for entry in written["accounts"]:
            if entry["email"] in emails:
                entry["warm_folders"] = True
        path.write_text(json.dumps(written, indent=2))


def land(world, account, folder, tag):
    """One more message in `folder` on the server, which nothing here syncs. Its UID."""
    return world.server.deliver(account, folder, w.variant(LIKE, Message_ID=f"<{tag}@example.test>"))


def stored(account, folder, uid):
    c = store.conn()
    try:
        return c.execute("SELECT 1 FROM messages WHERE account=? AND folder=? AND uid=?",
                         (account, folder, uid)).fetchone() is not None
    finally:
        c.close()


def examined(world, since, account):
    """The folders `account`'s sessions opened since `since`, in order."""
    return [str(c["args"][0]) for c in world.server.commands[since:]
            if c["user"] == account and c["command"] in ("SELECT", "EXAMINE")]


def logins(world, since):
    """Whose servers were asked anything since `since`."""
    return {c["user"] for c in world.server.commands[since:] if c["command"] == "LOGIN"}


@pytest.fixture
def warming_off(monkeypatch):
    """MAILMIND_WARM_FOLDERS=0 in the server's environment as it starts."""
    monkeypatch.setenv("MAILMIND_WARM_FOLDERS", "0")


@pytest.fixture
def warming_bea(monkeypatch):
    """MAILMIND_WARM_FOLDERS naming Bea, and an entry that is no address, which names no one."""
    monkeypatch.setenv("MAILMIND_WARM_FOLDERS", f" {w.BEA}, not-an-address ")


def test_nothing_beyond_the_inbox_is_warmed_unless_asked(app, world):
    uid = land(world, w.ADA, "Drafts", "draft-unasked")
    since = world.mark()
    app.work("warm")
    assert not stored(w.ADA, "Drafts", uid)
    assert world.server.commands[since:] == []             # not even a LIST


def test_an_account_asking_has_its_drafts_every_round_its_archive_every_second_and_its_sent_every_third(app, world):
    asking(app, world, w.ADA)
    since = world.mark()
    rounds, listed = [], []
    for n in range(1, 5):
        landed = {folder: land(world, w.ADA, folder, f"{folder.lower()}-{n}") for folder in ("Drafts", "Archive", "Sent")}
        began = world.mark()
        app.work("warm")
        rounds.append(sorted(folder for folder, uid in landed.items() if stored(w.ADA, folder, uid)))
        listed.append(bool([c for c in world.server.commands[began:] if c["command"] == "LIST"]))
    assert rounds == [["Drafts"], ["Archive", "Drafts"], ["Drafts", "Sent"], ["Archive", "Drafts"]]
    # Which folder plays each part was found in the first round and kept; and no one else was asked anything.
    assert listed == [True, False, False, False]
    assert logins(world, since) == {w.ADA}


def test_a_gmail_accounts_all_mail_holds_its_inbox_so_it_isnt_warmed(app, world):
    asking(app, world, w.CY)
    since = world.mark()
    for _ in range(6):
        app.work("warm")
    assert set(examined(world, since, w.CY)) == {"[Gmail]/Drafts", "[Gmail]/Sent Mail"}


def test_the_variable_off_warms_no_one_whatever_the_file_asks(warming_off, app, world):
    asking(app, world, w.ADA)
    uid = land(world, w.ADA, "Drafts", "draft-variable-off")
    since = world.mark()
    app.work("warm")
    assert not stored(w.ADA, "Drafts", uid)
    assert world.server.commands[since:] == []


def test_the_variable_names_the_accounts_warmed_over_the_file(warming_bea, app, world):
    asking(app, world, w.ADA)
    ada, bea = land(world, w.ADA, "Drafts", "draft-ada"), land(world, w.BEA, "Drafts", "draft-bea")
    since = world.mark()
    app.work("warm")
    assert (stored(w.ADA, "Drafts", ada), stored(w.BEA, "Drafts", bea)) == (False, True)
    assert logins(world, since) == {w.BEA}


def test_a_sync_round_warms_as_it_ends_after_the_inbox(app, world):
    asking(app, world, w.ADA)
    inbox, draft = land(world, w.ADA, "INBOX", "inbox-then-draft"), land(world, w.ADA, "Drafts", "draft-after-inbox")
    since = world.mark()
    assert app.post("/api/refresh", {}).json["ok"]
    app.settle()
    assert stored(w.ADA, "INBOX", inbox) and stored(w.ADA, "Drafts", draft)
    assert examined(world, since, w.ADA) == ["INBOX", "Drafts"]
