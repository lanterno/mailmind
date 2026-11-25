"""The IMAP a common action costs, held to the [counts] in budgets.toml (docs/test-strategy.md, section 5): each test does
one thing over HTTP, as the page does, counts what the server under test then asked of the fake IMAP server, and fails
when that is over its limit. No tolerance: a count is the same on every run and every machine. A limit is what was
measured when it was set, a promise not to get worse; a change that does better lowers it in the same commit.

Each entry's name is here, which tests/unit/test_budgets_file.py looks for (`budgets.unread`).
"""
from datetime import timedelta
from email.message import EmailMessage
from email.policy import SMTP

import budgets
from fixtures import world as w

# A connection's opening and closing, which a count leaves out: whether an action opens a connection of its own or takes
# one the pool kept is the pool's business, not the action's. The Python app logs in for each job and logs out after it;
# the Rust server keeps its connection and asks NOOP before it takes it again. Neither is the IMAP an action costs.
SETUP = {"CAPABILITY", "STARTTLS", "LOGIN", "AUTHENTICATE", "ENABLE", "ID", "NOOP", "LOGOUT"}

MOVE_ONE = "imap_move_one"
MARK_READ_ONE = "imap_mark_read_one"
DELTA_SYNC = "imap_delta_sync_nothing_new"
FIRST_SYNC = "imap_first_sync_small_account"
FIRST_SYNC_BYTES = "imap_first_sync_bytes_per_message"
REPLY = "imap_reply_filed_to_sent"

DEE = "dee@example.test"            # not one of the world's accounts
CONFIRM = "ada-inbox-02-lunch-confirm.eml"


def limit(name):
    return budgets.load()["counts"][name]["limit"]


def asked(world, since, user):
    """What `user`'s connections asked of the fake IMAP server since `since`, setup left out."""
    return [c for c in world.server.commands[since:] if c["user"] == user and c["command"] not in SETUP]


def listed(commands):
    return "\n".join(f"  [{c['mailbox']}] {c['line'].split(' ', 1)[1]}  ({c['bytes']} bytes back)" for c in commands)


def within(name, count, commands):
    held = limit(name)
    assert count <= held, f"{name}: {count}, over its limit of {held} in budgets.toml. What was sent:\n{listed(commands)}"


def test_moving_one_message(app, world):
    since = world.mark()
    r = app.post("/api/move", {**world.copy("ada-inbox-05-receipt.eml"), "to": "Archive"})
    assert r.status == 200 and r.json["ok"], r.json
    app.settle()
    sent = asked(world, since, w.ADA)
    within(MOVE_ONE, len(sent), sent)


def test_marking_one_message_read(app, world):
    since = world.mark()
    r = app.post("/api/seen", {**world.copy(CONFIRM), "seen": True})
    assert r.status == 200 and r.json["ok"], r.json
    app.settle()
    sent = asked(world, since, w.ADA)
    within(MARK_READ_ONE, len(sent), sent)


def test_a_sync_with_nothing_new(app, world):
    # The world's database was built from what the fake server holds, so Ada's Inbox has nothing new to sync.
    since = world.mark()
    app.work("sync", w.ADA, "INBOX")
    sent = asked(world, since, w.ADA)
    within(DELTA_SYNC, len(sent), sent)


def with_pdf(message_id):
    """A note with a PDF of about 100 KiB: longer than the first 64 KiB a sync reads of a message."""
    m = EmailMessage(policy=SMTP)
    m["From"], m["To"], m["Subject"] = "Lin Mori <lin@example.test>", f"Dee Lund <{DEE}>", "The plans"
    m["Date"], m["Message-ID"] = "Fri, 30 Oct 2026 09:00:00 +0000", message_id
    m.set_content("The plans, attached.")
    m.add_attachment(b"%PDF-1.4\n" + bytes(100 * 1024), maintype="application", subtype="pdf", filename="plans.pdf")
    return m.as_bytes()


def dee_synced_first(app, world):
    """Dee's account added with three messages in its Inbox, two short and one long, and its Inbox's first sync run:
    what that asked, and how many messages it found."""
    entry = world.server.add_account(DEE)
    for n in range(2):
        world.server.deliver(DEE, "INBOX", w.variant(CONFIRM, Message_ID=f"<dee-{n}@example.test>"))
    world.server.deliver(DEE, "INBOX", with_pdf("<dee-plans@example.test>"))
    added = app.post("/api/accounts/add", {"email": DEE, "type": "imap", "host": entry["host"], "port": entry["port"],
                                            "starttls": entry["starttls"],
                                            "credential": {"style": "keychain", "value": "app-password"}})
    assert added.status == 200, added.json
    app.settle()
    since = world.mark()
    app.work("sync", DEE, "INBOX")
    stored = app.get(f"/api/messages?account={DEE}&folder=INBOX").json["items"]
    assert len(stored) == 3, stored
    return asked(world, since, DEE), len(stored)


def test_an_accounts_first_sync(app, world):
    sent, _ = dee_synced_first(app, world)
    within(FIRST_SYNC, len(sent), sent)


def test_what_an_accounts_first_sync_reads_of_each_message(app, world):
    # The bytes the fake server sent back to what the sync asked, setup left out, over the messages it stored. Each
    # answer's tagged line repeats the tag the client chose, whose length is the client's and grows with its count of
    # commands, so a kept connection's would make the sum drift: the tag is left out.
    sent, stored = dee_synced_first(app, world)
    read = sum(c["bytes"] - len(c["line"].split(" ", 1)[0]) for c in sent)
    within(FIRST_SYNC_BYTES, read // stored, sent)


def test_a_reply_sent_and_filed_in_sent(app, world):
    lunch = world.key("ada-inbox-01-lunch.eml")
    r = app.post("/api/send", {"account": w.ADA, "mode": "reply", "original_key": lunch, "to": ["lin@example.test"],
                               "subject": "Re: Lunch", "text": "Great, see you then.", "idempotency_key": "k-count"})
    assert r.status == 200 and r.json["queued"], r.json
    since = world.mark()
    app.clock.shift(timedelta(seconds=r.json["undo_seconds"] + 1))
    app.work("outbox")
    assert len(world.smtp.messages) == 1
    assert "Re: Lunch" in [m.headers()["Subject"] for m in world.server.messages(w.ADA, "Sent").values()]
    sent = asked(world, since, w.ADA)
    within(REPLY, len(sent), sent)

