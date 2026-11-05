"""A message's key: what makes rows copies of one message, what it keeps through a move, an Undo, a
renumber and a second folder, how long it waits for a copy to come back, and where the API shows it."""
import sqlite3

from fixtures import world as w
from mailmind.mailbox.domain import identity
from mailmind.platform.db import migrate, migrations

LUNCH = "ada-inbox-01-lunch.eml"
DAY = 86400


def q(sql, *args):
    import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def execute(sql, *args):
    import store
    c = store.conn()
    try:
        c.execute(sql, args)
        c.commit()
    finally:
        c.close()


def assess():
    import summarize
    return summarize.summarize_pending(limit=100, workers=1)


def copies(key):
    return q("SELECT folder FROM messages WHERE message_key=? ORDER BY folder", key)


def said(pk):
    return [r["summary"] for r in q("SELECT summary FROM summaries WHERE message_pk=?", pk)]


# -- what a key is ------------------------------------------------------------------------------------

def test_the_identity_is_the_message_id_without_the_whitespace_a_fold_left():
    assert identity.of("<a@example.test>", "x", "y", "z") == "<a@example.test>"
    assert identity.of(" <a@\r\n example.test>\n", "x", "y", "z") == "<a@example.test>"


def test_a_message_with_no_id_is_known_by_its_envelope():
    one = identity.of("", "bo@example.test", "2026-11-02T10:00:00+00:00", "Hello")
    assert one == identity.of(None, "bo@example.test", "2026-11-02T10:00:00+00:00", "Hello")
    assert one.startswith("~") and one != identity.of("", "bo@example.test", "2026-11-02T10:00:00+00:00", "Goodbye")


def test_copies_in_two_folders_share_a_key_and_two_accounts_dont(world):
    first = world.row(LUNCH)
    raw = w.variant(LUNCH)
    world.deliver(w.ADA, "Archive", raw)
    world.deliver(w.BEA, "INBOX", raw)
    assert copies(first["message_key"]) == [{"folder": "Archive"}, {"folder": "INBOX"}]
    [theirs] = q("SELECT message_key FROM messages WHERE account=? AND message_id=?", w.BEA, first["message_id"])
    assert theirs["message_key"] != first["message_key"]


def test_a_message_with_no_id_gets_a_key_of_its_own(world):
    uid = world.deliver(w.ADA, "INBOX", b"From: Bo <bo@example.test>\r\nSubject: No id\r\n"
                                        b"Date: Mon, 02 Nov 2026 10:00:00 +0000\r\n\r\nHello\r\n")
    [row] = q("SELECT message_key FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.ADA, uid)
    assert row["message_key"] is not None and row["message_key"] != world.row(LUNCH)["message_key"]


# -- what it keeps ------------------------------------------------------------------------------------

def test_a_message_archived_then_undone_keeps_its_assessment_with_no_new_call(app, world, ai):
    assess()
    before, asked = world.row(LUNCH), len(ai.requests)
    assert asked and said(before["pk"])
    told = said(before["pk"])
    assert app.post("/api/archive", {"pk": before["pk"]}).json["ok"]
    assert app.post("/api/undo", {}).json["ok"]
    [after] = q("SELECT pk, message_key FROM messages WHERE account=? AND folder='INBOX' AND message_id=?",
                w.ADA, before["message_id"])
    assert after["pk"] != before["pk"]
    assert after["message_key"] == before["message_key"]
    assert said(after["pk"]) == told
    assess()
    assert len(ai.requests) == asked


def test_a_renumbered_folder_keeps_its_assessments_with_no_new_call(app, world, ai):
    import accounts
    import ingest
    assess()
    before, asked = world.row(LUNCH), len(ai.requests)
    told = said(before["pk"])
    assert told
    world.server.renumber(w.ADA, "INBOX")
    ingest.sync_new(accounts.get(w.ADA))
    [after] = q("SELECT pk, message_key FROM messages WHERE account=? AND folder='INBOX' AND message_id=?",
                w.ADA, before["message_id"])
    assert after["message_key"] == before["message_key"] and said(after["pk"]) == told
    assess()
    assert len(ai.requests) == asked


def test_a_copy_that_arrives_later_finds_what_the_ai_said(app, world, ai):
    assess()
    first, asked = world.row(LUNCH), len(ai.requests)
    world.deliver(w.ADA, "Archive", w.variant(LUNCH))
    [copy] = q("SELECT pk FROM messages WHERE account=? AND folder='Archive' AND message_id=?",
               w.ADA, first["message_id"])
    assert said(copy["pk"]) == said(first["pk"]) != []
    assess()
    assert len(ai.requests) == asked


def test_two_copies_waiting_to_be_assessed_cost_one_call(app, world, ai):
    assess()
    asked = len(ai.requests)
    raw = w.variant(LUNCH, Message_ID="<twice@example.test>", Subject="Twice")
    world.deliver(w.ADA, "INBOX", raw)
    world.deliver(w.ADA, "Archive", raw)
    assert assess() == 1
    assert len(ai.requests) == asked + 1
    told = [r["summary"] for r in q("SELECT s.summary FROM summaries s JOIN messages m ON m.pk=s.message_pk "
                                    "WHERE m.message_id='<twice@example.test>'")]
    assert len(told) == 2 and told[0] == told[1]


# -- how long it waits --------------------------------------------------------------------------------

def gone_since(key):
    return q("SELECT gone_at FROM message_identity WHERE key=?", key)[0]["gone_at"]


def forget(now):
    import store
    c = store.conn()
    try:
        n = store.forget_gone_messages(c, now)
        c.commit()
        return n
    finally:
        c.close()


def test_a_key_waits_a_month_for_a_copy_to_come_back(app, world, ai):
    import store
    assess()
    row = world.row(LUNCH)
    key = row["message_key"]
    assert gone_since(key) is None
    c = store.conn()
    store.delete_message(c, row["pk"])
    c.commit()
    c.close()
    gone = gone_since(key)
    assert gone is not None
    assert forget(gone + 29 * DAY) == 0
    assert len(q("SELECT 1 FROM assessments WHERE message_key=?", key)) == 1
    assert forget(gone + 31 * DAY) == 1
    assert q("SELECT 1 FROM message_identity WHERE key=?", key) == []
    assert q("SELECT 1 FROM assessments WHERE message_key=?", key) == []


def test_a_copy_that_comes_back_ends_the_wait(world):
    import store
    row = world.row(LUNCH)
    key = row["message_key"]
    c = store.conn()
    store.delete_message(c, row["pk"])
    c.commit()
    c.close()
    assert gone_since(key) is not None
    world.deliver(w.ADA, "Archive", w.variant(LUNCH))
    assert gone_since(key) is None
    assert forget(10**10) == 0


def test_a_key_with_a_copy_left_isnt_gone(world):
    import store
    row = world.row(LUNCH)
    world.deliver(w.ADA, "Archive", w.variant(LUNCH))
    c = store.conn()
    store.delete_message(c, row["pk"])
    c.commit()
    c.close()
    assert gone_since(row["message_key"]) is None


def test_the_forget_job_ends_a_key_long_gone(world):
    import server
    import store
    row = world.row(LUNCH)
    c = store.conn()
    store.delete_message(c, row["pk"])
    c.execute("UPDATE message_identity SET gone_at=1 WHERE key=?", (row["message_key"],))
    c.commit()
    c.close()
    server.forget_round()
    assert q("SELECT 1 FROM message_identity WHERE key=?", row["message_key"]) == []


# -- a row an older commit wrote --------------------------------------------------------------------------

def test_a_start_gives_a_key_to_a_row_an_older_commit_wrote(world):
    import store
    row = world.row(LUNCH)
    execute("UPDATE messages SET message_key=NULL WHERE pk=?", row["pk"])
    execute("INSERT INTO summaries(message_pk, summary, importance) VALUES (?, 'Said by the old one', 3)", row["pk"])
    store.init()
    assert world.row(LUNCH)["message_key"] == row["message_key"]
    assert q("SELECT summary FROM assessments WHERE message_key=?", row["message_key"]) == [
        {"summary": "Said by the old one"}]


def test_migration_30_gives_the_keys_and_keeps_what_the_ai_said(tmp_path):
    path = str(tmp_path / "old.db")
    migrate.init(path, migrations.MIGRATIONS[:29])
    c = sqlite3.connect(path)
    rows = [(1, "INBOX", 1, "<a@example.test>"), (2, "Archive", 7, "<a@example.test>"), (3, "INBOX", 2, ""),
            (4, "INBOX", 3, None)]
    c.executemany("INSERT INTO messages(pk, account, kind, folder, uid, message_id, from_addr, subject, date_iso) "
                  "VALUES (?, 'ada@example.test', 'imap', ?, ?, ?, 'bo@example.test', 'Hello', '2026-11-02')", rows)
    c.execute("INSERT INTO summaries(message_pk, summary, importance, created_at) VALUES (2, 'Said once', 3, 't')")
    c.commit()
    c.close()
    migrate.init(path)
    c = sqlite3.connect(path)
    try:
        keys = dict(c.execute("SELECT pk, message_key FROM messages"))
        assert keys[1] == keys[2] is not None
        assert keys[3] == keys[4] is not None and keys[3] != keys[1]     # the same envelope, no id
        assert c.execute("SELECT message_key, summary FROM assessments").fetchall() == [(keys[1], "Said once")]
    finally:
        c.close()


def test_migration_30_can_run_again(tmp_path):
    path = str(tmp_path / "old.db")
    migrate.init(path)
    c = sqlite3.connect(path)
    c.execute("PRAGMA user_version=29")
    c.commit()
    c.close()
    migrate.init(path)
    c = sqlite3.connect(path)
    try:
        assert c.execute("PRAGMA user_version").fetchone()[0] == migrations.MIGRATIONS[-1][0]
    finally:
        c.close()


# -- where the API shows it -------------------------------------------------------------------------------

def test_every_list_of_mail_gives_the_key_beside_the_pk(app, world, ai):
    import store
    assess()
    row = world.row(LUNCH)
    pk, key = row["pk"], row["message_key"]

    def holds(items):
        return [i["key"] for i in items if i["pk"] == pk] == [key]
    assert holds(app.get(f"/api/messages?account={w.ADA}&folder=INBOX&limit=100").json["items"])
    assert holds(app.get(f"/api/messages?account={w.ADA}&folder=INBOX&limit=100&group=1").json["items"])
    assert holds(app.get(f"/api/thread?account={w.ADA}&thread_id={row['thread_id']}").json["items"])
    assert holds(app.get(f"/api/search?q=lunch&scope=all&account={w.ADA}").json["items"])
    assert holds(app.get(f"/api/digest?account={w.ADA}").json["items"])
    assert app.get(f"/api/message/{pk}/body").json["key"] == key
    c = store.conn()
    store.mute_thread(c, w.ADA, row["thread_id"], "now")
    c.commit()
    c.close()
    assert [i["key"] for i in app.get(f"/api/messages?account={w.ADA}&folder=__muted__&limit=100").json["items"]
            if i["thread_id"] == row["thread_id"]] != [None]
