"""What a delivered reply does to its original, \\Answered and a follow-up, goes by what outlasts a row: the Message-ID and
the account the outbox row keeps (`orig_*`), or for a message with no Message-ID its key (`original_key`). The pk the original's
row had when the request named it may be another message's by then."""
from datetime import timedelta

import pytest

from fixtures import world as w
from mailmind.bootstrap import accounts
from mailmind.bootstrap.compose import outbox
from mailmind.bootstrap.mailbox import ingest, store
from mailmind.platform.db.migrations import m0044_outbox_original_key

LUNCH = "ada-inbox-02-lunch-confirm.eml"
OTHER = "ada-inbox-03-newsletter.eml"
QUILT = "ada-inbox-05-receipt.eml"


def query(sql, *args):
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args).fetchall()]
    finally:
        c.close()


def execute(sql, *args):
    c = store.conn()
    try:
        c.execute(sql, args)
        c.commit()
    finally:
        c.close()


def reply(app, pk, key="k-reply"):
    """Reply to the message row `pk` holds now, named as the page names it: by its key."""
    r = app.post("/api/send", {"account": w.ADA, "mode": "reply", "original_key": w.copy_of(pk)["key"], "to": ["lin@example.test"],
                               "subject": "Re: Lunch on Thursday?", "text": "See you.", "idempotency_key": key}).json
    assert r["ok"], r
    return r["outbox_id"]


def goes_out(app):
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()


def another_message_takes(pk, other):
    """The row `other` comes to hold the pk a queued send names."""
    c = store.conn()
    try:
        c.execute("PRAGMA foreign_keys=OFF")               # what hangs off the row isn't what's being asked about
        c.execute("UPDATE messages SET pk=? WHERE pk=?", (pk, other))
        c.commit()
    finally:
        c.close()


def answered_on_server(world, name):
    [(_, _, _, flags)] = world.where(name)["server"]
    return "\\Answered" in flags


def followups():
    return query("SELECT thread_id, kind FROM followups ORDER BY thread_id")


def held_for_a_reply(app, world):
    """A reply on its way out, its original archived and gone from the database, and another message on its old pk."""
    original = world.row(LUNCH)
    reply(app, original["pk"])
    assert app.post("/api/archive", w.copy_of(original["pk"])).json["ok"]
    assert query("SELECT 1 FROM messages WHERE pk=?", original["pk"]) == []
    bystander = world.row(OTHER)
    another_message_takes(original["pk"], bystander["pk"])
    return original, bystander


# -- going by the Message-ID -------------------------------------------------------------------

def test_the_original_keeps_answered_when_its_pk_has_gone_to_another_message(app, world):
    held_for_a_reply(app, world)
    goes_out(app)
    [(_, box, _, flags)] = world.where(LUNCH)["server"]
    assert (box, "\\Answered" in flags) == ("Archive", True)
    assert not answered_on_server(world, OTHER)
    assert [r["answered"] for r in world.where(OTHER)["db"]] == [0]


def test_a_follow_up_goes_on_the_replied_thread_when_the_pk_has_gone_to_another_message(app, world):
    original, bystander = held_for_a_reply(app, world)
    assert original["thread_id"] != bystander["thread_id"]
    goes_out(app)
    assert [f["thread_id"] for f in followups()] == [original["thread_id"]]


def test_a_renumbered_original_is_answered_and_followed_up_on_the_row_it_has_now(app, world):
    original = world.row(LUNCH)
    reply(app, original["pk"])
    world.server.renumber(w.ADA, "INBOX")
    ingest.sync_new(accounts.get(w.ADA))
    now = world.row(LUNCH)
    assert now["pk"] != original["pk"]
    goes_out(app)
    assert answered_on_server(world, LUNCH) and world.row(LUNCH)["answered"] == 1
    assert followups() == [{"thread_id": original["thread_id"], "kind": "sent"}]


def test_the_row_names_its_original_by_what_outlasts_a_row(app, world):
    original = world.row(LUNCH)
    oid = reply(app, original["pk"])
    [row] = query("SELECT orig_account, orig_message_id, original_key FROM outbox WHERE id=?", oid)
    assert row == {"orig_account": w.ADA, "orig_message_id": original["message_id"], "original_key": original["message_key"]}


def test_a_mail_that_was_not_renumbered_is_answered_and_followed_up_as_ever(app, world):
    original = world.row(LUNCH)
    reply(app, original["pk"])
    goes_out(app)
    assert answered_on_server(world, LUNCH) and world.row(LUNCH)["answered"] == 1
    assert followups() == [{"thread_id": original["thread_id"], "kind": "sent"}]


# -- a message with no Message-ID to go by ----------------------------------------------------

def no_message_id(world):
    """A message that came with no Message-ID, and its row."""
    raw = w.raw(QUILT).replace(b"Message-ID:", b"X-Was-Message-ID:", 1)
    uid = world.deliver(w.ADA, "INBOX", raw)
    [row] = query("SELECT pk, thread_id FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.ADA, uid)
    return uid, row


def flags_of(world, uid):
    with world.server.lock:
        return world.server.mailbox(w.ADA, "INBOX").messages[uid].flags


def test_a_reply_to_a_message_with_no_message_id_goes_by_its_key(app, world):
    uid, row = no_message_id(world)
    oid = reply(app, row["pk"])
    [queued] = query("SELECT orig_account, orig_message_id, original_key FROM outbox WHERE id=?", oid)
    assert queued["orig_account"] == w.ADA and not queued["orig_message_id"] and queued["original_key"]
    goes_out(app)
    assert "\\Answered" in flags_of(world, uid)
    assert followups() == [{"thread_id": row["thread_id"], "kind": "sent"}]


def test_a_message_with_no_message_id_is_still_found_after_a_renumber(app, world):
    uid, row = no_message_id(world)
    reply(app, row["pk"])
    another_message_takes(row["pk"] + 1000, row["pk"])                # the row has another pk now, and its old one is no one's
    goes_out(app)
    assert "\\Answered" in flags_of(world, uid)


def test_a_pk_that_has_gone_to_a_message_with_a_message_id_was_not_the_one_replied_to(app, world):
    uid, row = no_message_id(world)
    reply(app, row["pk"])
    execute("DELETE FROM messages WHERE pk=?", row["pk"])
    bystander = world.row(OTHER)
    another_message_takes(row["pk"], bystander["pk"])
    goes_out(app)
    assert not answered_on_server(world, OTHER)
    assert [r["answered"] for r in world.where(OTHER)["db"]] == [0]
    assert bystander["thread_id"] not in [f["thread_id"] for f in followups()]


def test_a_pk_that_has_gone_to_another_accounts_message_was_not_the_one_replied_to(app, world):
    uid, row = no_message_id(world)
    reply(app, row["pk"])
    execute("DELETE FROM messages WHERE pk=?", row["pk"])
    theirs = world.deliver(w.BEA, "INBOX", w.raw(QUILT).replace(b"Message-ID:", b"X-Was-Message-ID:", 1))
    [other] = query("SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.BEA, theirs)
    another_message_takes(row["pk"], other["pk"])
    goes_out(app)
    with world.server.lock:
        assert "\\Answered" not in world.server.mailbox(w.BEA, "INBOX").messages[theirs].flags


# -- a send already queued as migration 44 runs -----------------------------------------------------

def upgraded(oid, pk):
    """What migration 44 makes of the queue as it stands, with send `oid` naming `pk` in the column it read, which
    migration 45 ended."""
    c = store.conn()
    try:
        c.execute("ALTER TABLE outbox ADD COLUMN in_reply_to_pk INTEGER")
        c.execute("UPDATE outbox SET in_reply_to_pk=? WHERE id=?", (pk, oid))
        m0044_outbox_original_key.migrate(c)
        c.execute("ALTER TABLE outbox DROP COLUMN in_reply_to_pk")
        c.commit()
    finally:
        c.close()


def test_a_row_queued_before_migration_27_is_read_by_its_pk_as_it_upgrades(app, world):
    original = world.row(LUNCH)
    oid = reply(app, original["pk"])
    execute("UPDATE outbox SET orig_account=NULL, orig_message_id=NULL, orig_thread_id=NULL, original_key=NULL WHERE id=?", oid)
    upgraded(oid, original["pk"])
    [row] = query("SELECT orig_account, orig_message_id, orig_thread_id, original_key FROM outbox WHERE id=?", oid)
    assert row == {"orig_account": w.ADA, "orig_message_id": original["message_id"], "orig_thread_id": original["thread_id"],
                   "original_key": original["message_key"]}
    goes_out(app)
    assert answered_on_server(world, LUNCH) and world.row(LUNCH)["answered"] == 1
    assert followups() == [{"thread_id": original["thread_id"], "kind": "sent"}]


def test_a_reply_to_a_message_with_no_message_id_takes_its_key_as_it_upgrades(app, world):
    uid, row = no_message_id(world)
    oid = reply(app, row["pk"])
    [was] = query("SELECT original_key FROM outbox WHERE id=?", oid)
    execute("UPDATE outbox SET original_key=NULL WHERE id=?", oid)
    upgraded(oid, row["pk"])
    assert query("SELECT original_key FROM outbox WHERE id=?", oid) == [was]
    goes_out(app)
    assert "\\Answered" in flags_of(world, uid)


@pytest.mark.parametrize("name", ["with a Message-ID", "in another account"])
def test_a_pk_that_has_gone_to_another_message_gives_no_key_as_it_upgrades(app, world, name):
    uid, row = no_message_id(world)
    oid = reply(app, row["pk"])
    execute("UPDATE outbox SET original_key=NULL WHERE id=?", oid)
    execute("DELETE FROM messages WHERE pk=?", row["pk"])
    if name == "with a Message-ID":
        other = world.row(OTHER)["pk"]
    else:
        theirs = world.deliver(w.BEA, "INBOX", w.raw(QUILT).replace(b"Message-ID:", b"X-Was-Message-ID:", 1))
        [found] = query("SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.BEA, theirs)
        other = found["pk"]
    another_message_takes(row["pk"], other)
    upgraded(oid, row["pk"])
    assert query("SELECT original_key FROM outbox WHERE id=?", oid) == [{"original_key": None}]
    goes_out(app)
    assert "\\Answered" not in flags_of(world, uid)


def test_a_send_that_has_gone_is_left_as_it_was_by_the_upgrade(app, world):
    original = world.row(LUNCH)
    oid = reply(app, original["pk"])
    goes_out(app)
    execute("UPDATE outbox SET orig_account=NULL, orig_message_id=NULL, orig_thread_id=NULL, original_key=NULL WHERE id=?", oid)
    upgraded(oid, original["pk"])
    assert query("SELECT state, orig_account, original_key FROM outbox WHERE id=?", oid) == [
        {"state": "sent", "orig_account": None, "original_key": None}]
