"""A snooze is kept by message key and folder, so it outlives the row it was set on. And a message
that wakes is unread on the server too, or the next sync that read its flags would take that back."""
from datetime import datetime, timedelta, timezone

from fixtures import world as w
from mailmind.bootstrap import attention
from mailmind.platform import undo

RECEIPT = "ada-inbox-05-receipt.eml"
CI = "ada-inbox-04-ci.eml"                               # already read


def acct():
    from mailmind.bootstrap import accounts
    return accounts.get(w.ADA)


def until(app, hours=6):
    return int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp())


def snooze(app, world, name=RECEIPT, hours=6):
    assert app.post("/api/snooze", {"pk": world.pk(name), "until": until(app, hours)}).json["ok"]


def listed(app, folder="INBOX"):
    return {i["subject"] for i in app.get(f"/api/messages?account={w.ADA}&folder={folder}&limit=100").json["items"]}


def copy(world, name=RECEIPT, folder="INBOX"):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return c.execute("""SELECT m.pk, m.seen, z.until AS snooze_until FROM messages m
                            LEFT JOIN snoozes z ON z.message_key=m.message_key AND z.folder=m.folder
                            WHERE m.account=? AND m.folder=? AND m.message_id=?""",
                         (w.ADA, folder, w.message_id(name))).fetchone()
    finally:
        c.close()


def renumber(world):
    from mailmind.bootstrap.mailbox import ingest
    world.server.renumber(w.ADA, "INBOX")
    ingest.sync_new(acct())


def test_a_snoozed_message_stays_hidden_through_a_resync_of_its_folder(app, world):
    subject = world.row(RECEIPT)["subject"]
    snooze(app, world)
    before = copy(world)
    renumber(world)
    after = copy(world)
    assert after["pk"] != before["pk"] and after["snooze_until"] == before["snooze_until"]
    assert subject not in listed(app)
    assert subject in listed(app, "__snoozed__")


def test_the_sql_wakes_what_the_domain_calls_due(app, world):
    from mailmind.attention.domain.snooze import is_due
    from mailmind.bootstrap.mailbox import store
    snooze(app, world)
    c = store.conn()
    try:
        at = c.execute("SELECT until FROM snoozes").fetchone()["until"]
        for now in (at - 1, at, at + 1):
            assert bool(attention.book.due_snoozes(c, now)) is is_due(at, now)
    finally:
        c.close()


def test_a_time_too_large_to_be_a_number_is_refused_like_any_other_bad_time(app, world):
    r = app.post("/api/snooze", {"pk": world.pk(RECEIPT), "until": float("inf")})
    assert (r.status, r.json["ok"]) == (400, False)
    assert world.row(RECEIPT)["subject"] in listed(app)


def test_it_wakes_on_time_on_the_row_it_has_now(app, world):
    subject = world.row(RECEIPT)["subject"]
    snooze(app, world)
    renumber(world)
    app.clock.shift(timedelta(hours=7))
    attention.snoozing.wake_due()
    assert copy(world)["snooze_until"] is None
    assert subject in listed(app)


def test_undo_of_a_snooze_finds_the_row_it_has_now(app, world):
    subject = world.row(RECEIPT)["subject"]
    snooze(app, world)
    renumber(world)
    assert app.post("/api/undo", {}).json["ok"]
    assert copy(world)["snooze_until"] is None and subject in listed(app)
    renumber(world)
    assert copy(world)["snooze_until"] is None                # nor does the resync bring it back


def test_undo_from_a_stack_written_before_the_rows_were_named(app, world):
    from mailmind.bootstrap.mailbox import store
    pk = world.pk(RECEIPT)
    snooze(app, world)
    c = store.conn()
    undo.pop(c)
    undo.put_back(c, {"kind": "snooze", "pks": [pk], "prev_until": [None], "label": "Snoozed"})
    c.commit()
    c.close()
    assert app.post("/api/undo", {}).json["ok"]
    assert copy(world)["snooze_until"] is None


def test_unsnoozing_ends_it_for_good(app, world):
    snooze(app, world)
    assert app.post("/api/unsnooze", {"pk": world.pk(RECEIPT)}).json["ok"]
    renumber(world)
    assert copy(world)["snooze_until"] is None


def test_a_snoozed_message_filed_elsewhere_isnt_hidden_there(app, world):
    from mailmind.bootstrap.mailbox import ingest
    subject = world.row(RECEIPT)["subject"]
    snooze(app, world)
    assert app.post("/api/archive", {"pk": world.pk(RECEIPT)}).json["ok"]
    ingest.sync_folder(acct(), "Archive")
    assert copy(world, folder="Archive")["snooze_until"] is None
    assert subject in listed(app, "Archive")


def test_undo_of_filing_it_brings_the_snooze_back_if_it_hasnt_come_due(app, world):
    from mailmind.bootstrap.mailbox import ingest
    subject = world.row(RECEIPT)["subject"]
    snooze(app, world)
    assert app.post("/api/archive", {"pk": world.pk(RECEIPT)}).json["ok"]
    assert app.post("/api/undo", {}).json["ok"]
    ingest.sync_new(acct())
    assert copy(world)["snooze_until"] is not None and subject not in listed(app)


def test_one_that_comes_due_while_the_message_is_filed_elsewhere_is_over(app, world):
    from mailmind.bootstrap.mailbox import ingest
    from mailmind.bootstrap.mailbox import store
    snooze(app, world)
    assert app.post("/api/archive", {"pk": world.pk(RECEIPT)}).json["ok"]
    app.clock.shift(timedelta(hours=7))
    attention.snoozing.wake_due()
    c = store.conn()
    left = c.execute("SELECT COUNT(*) FROM snoozes").fetchone()[0]
    c.close()
    assert left == 0
    assert app.post("/api/undo", {}).json["ok"]
    ingest.sync_new(acct())
    assert copy(world)["snooze_until"] is None


def test_a_forgotten_message_takes_its_snooze_with_it(world):
    from mailmind.bootstrap.mailbox import store
    from mailmind.platform import settings
    assert settings.current()
    c = store.conn()
    pk = world.pk(RECEIPT)
    key = world.row(RECEIPT)["message_key"]
    attention.book.set_snooze(c, pk, 4102444800)
    store.delete_message(c, pk)
    c.execute("UPDATE message_identity SET gone_at=1 WHERE key=?", (key,))
    store.forget_gone_messages(c, 4102444800)
    assert c.execute("SELECT COUNT(*) FROM snoozes").fetchone()[0] == 0
    c.commit()
    c.close()


def test_waking_marks_read_mail_unread_on_the_server(app, world):
    from mailmind.bootstrap.mailbox import commands
    snooze(app, world, CI)
    app.clock.shift(timedelta(hours=7))
    attention.snoozing.wake_due()
    assert copy(world, CI)["seen"] == 0
    commands.drain()
    [(_, _, _, flags)] = world.where(CI)["server"]
    assert "\\Seen" not in flags and copy(world, CI)["seen"] == 0


def test_a_sync_before_the_mark_lands_keeps_it_unread(app, world):
    from mailmind.bootstrap.mailbox import ingest
    world.server.without.add("CONDSTORE")                       # so the sync reads the newest's flags
    snooze(app, world, CI)
    app.clock.shift(timedelta(hours=7))
    attention.snoozing.wake_due()
    ingest.sync_new(acct())
    assert copy(world, CI)["seen"] == 0


def test_a_server_that_refuses_the_mark_doesnt_lose_the_message(app, world):
    from mailmind.bootstrap.mailbox import commands
    snooze(app, world, CI)
    app.clock.shift(timedelta(hours=7))
    attention.snoozing.wake_due()
    world.server.fail("UID STORE", user=w.ADA)
    commands.drain()
    assert copy(world, CI)["seen"] == 1                         # as the server has it
    assert app.get("/api/pending").json["stuck"] == 1
    assert world.row(CI)["subject"] in listed(app)


def test_waking_unread_mail_asks_nothing_of_the_server(app, world):
    snooze(app, world)
    app.clock.shift(timedelta(hours=7))
    attention.snoozing.wake_due()
    assert app.get("/api/pending").json["items"] == []


def test_read_only_wakes_and_the_mark_waits_in_the_queue(world, monkeypatch, app):
    from mailmind.bootstrap.mailbox import commands
    monkeypatch.setenv("MAILMIND_PROFILE", "read-only")
    snooze(app, world, CI)
    app.clock.shift(timedelta(hours=7))
    since = world.mark()
    attention.snoozing.wake_due()
    commands.drain()
    assert world.writes(since) == []
    assert copy(world, CI)["seen"] == 0
    assert app.get("/api/pending").json["waiting"] == 1


def test_two_copies_waking_together_each_get_the_mark(app, world):
    from mailmind.bootstrap.mailbox import store
    snooze(app, world, CI)
    c = store.conn()
    row = dict(c.execute("SELECT * FROM messages WHERE pk=?", (world.pk(CI),)).fetchone())
    other = {k: v for k, v in row.items() if k != "pk"}
    other.update(folder="Archive", uid=row["uid"] + 900)
    pk = c.execute(f"INSERT INTO messages({','.join(other)}) VALUES({','.join('?' * len(other))})", tuple(other.values())).lastrowid
    attention.book.set_snooze(c, pk, until(app))
    c.commit()
    c.close()
    app.clock.shift(timedelta(hours=7))
    attention.snoozing.wake_due()
    assert copy(world, CI)["seen"] == 0 and copy(world, CI, "Archive")["seen"] == 0


# -- two copies in one folder ------------------------------------------------------------------------------

def second_copy(world, name=RECEIPT):
    """The same message under another UID in the same folder, stored as ingest stores one."""
    from mailmind.bootstrap.mailbox import store
    r = world.row(name)
    fields = ("account", "kind", "folder", "message_id", "gmail_thread", "from_addr", "from_name", "subject", "date_iso", "ts",
              "snippet", "bucket", "is_junk")
    c = store.conn()
    try:
        pk = store.upsert_message(c, {**{k: r[k] for k in fields}, "uid": r["uid"] + 900})
        c.commit()
        return pk
    finally:
        c.close()


def test_a_snooze_and_its_end_reach_every_copy_in_the_folder(world):
    from mailmind.bootstrap.mailbox import store
    one, other = world.pk(RECEIPT), second_copy(world)
    c = store.conn()
    try:
        def wakes():
            return [store.get_message(c, pk)["snooze_until"] for pk in (one, other)]
        attention.book.set_snooze(c, one, 4102444800)
        assert wakes() == [4102444800, 4102444800]
        assert c.execute("SELECT until FROM snoozes").fetchall()[0][0] == 4102444800
        attention.book.clear_snooze(c, other)
        assert wakes() == [None, None]
        assert c.execute("SELECT COUNT(*) FROM snoozes").fetchone()[0] == 0
    finally:
        c.rollback()
        c.close()
