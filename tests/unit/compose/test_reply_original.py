"""What a delivered reply does to its original, \\Answered and a follow-up, goes by what outlasts a row: the Message-ID and
the account the outbox row keeps (`orig_*`). The pk it also keeps may be another message's by then. Only a row with no
Message-ID to go by goes by that pk: one queued before migration 27, or a reply to a message that has none."""
from datetime import timedelta

from fixtures import world as w
from mailmind.bootstrap import accounts
from mailmind.bootstrap.compose import outbox
from mailmind.bootstrap.mailbox import ingest, store

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
    r = app.post("/api/send", {"account": w.ADA, "mode": "reply", "in_reply_to_pk": pk, "to": ["lin@example.test"],
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
    return query("SELECT thread_id, kind, anchor_pk FROM followups ORDER BY thread_id")


def held_for_a_reply(app, world):
    """A reply on its way out, its original archived and gone from the database, and another message on its old pk."""
    original = world.row(LUNCH)
    reply(app, original["pk"])
    assert app.post("/api/archive", {"pk": original["pk"]}).json["ok"]
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
    assert followups()[0]["anchor_pk"] is None             # no row of its own left to name


def test_a_renumbered_original_is_answered_and_followed_up_on_the_row_it_has_now(app, world):
    original = world.row(LUNCH)
    reply(app, original["pk"])
    world.server.renumber(w.ADA, "INBOX")
    ingest.sync_new(accounts.get(w.ADA))
    now = world.row(LUNCH)
    assert now["pk"] != original["pk"]
    goes_out(app)
    assert answered_on_server(world, LUNCH) and world.row(LUNCH)["answered"] == 1
    assert followups() == [{"thread_id": original["thread_id"], "kind": "sent", "anchor_pk": now["pk"]}]


def test_the_row_still_names_the_pk_it_was_queued_with(app, world):
    pk = world.pk(LUNCH)
    oid = reply(app, pk)
    [row] = query("SELECT in_reply_to_pk, orig_account, orig_message_id FROM outbox WHERE id=?", oid)
    assert row == {"in_reply_to_pk": pk, "orig_account": w.ADA, "orig_message_id": world.row(LUNCH)["message_id"]}


def test_a_mail_that_was_not_renumbered_is_answered_and_followed_up_as_ever(app, world):
    original = world.row(LUNCH)
    reply(app, original["pk"])
    goes_out(app)
    assert answered_on_server(world, LUNCH) and world.row(LUNCH)["answered"] == 1
    assert followups() == [{"thread_id": original["thread_id"], "kind": "sent", "anchor_pk": original["pk"]}]


# -- the two rows with no Message-ID to go by ----------------------------------------------------

def test_a_row_queued_before_migration_27_still_goes_by_its_pk(app, world):
    original = world.row(LUNCH)
    oid = reply(app, original["pk"])
    execute("UPDATE outbox SET orig_account=NULL, orig_message_id=NULL, orig_thread_id=NULL WHERE id=?", oid)
    goes_out(app)
    assert answered_on_server(world, LUNCH) and world.row(LUNCH)["answered"] == 1
    assert followups() == [{"thread_id": original["thread_id"], "kind": "sent", "anchor_pk": original["pk"]}]


def no_message_id(world):
    """A message that came with no Message-ID, and its row."""
    raw = w.raw(QUILT).replace(b"Message-ID:", b"X-Was-Message-ID:", 1)
    uid = world.deliver(w.ADA, "INBOX", raw)
    [row] = query("SELECT pk, thread_id FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.ADA, uid)
    return uid, row


def flags_of(world, uid):
    with world.server.lock:
        return world.server.mailbox(w.ADA, "INBOX").messages[uid].flags


def test_a_reply_to_a_message_with_no_message_id_still_goes_by_its_pk(app, world):
    uid, row = no_message_id(world)
    oid = reply(app, row["pk"])
    [queued] = query("SELECT orig_account, orig_message_id FROM outbox WHERE id=?", oid)
    assert queued["orig_account"] == w.ADA and not queued["orig_message_id"]
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
