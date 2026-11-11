"""A list's badge says what opening its conversation shows: every folder's messages of the conversation, each once, and `count` stays
how many of it the list itself holds, which is what filing that row takes. All addresses are fictional."""
import itertools
from urllib.parse import quote

from fixtures import world as w
from mailmind.bootstrap import attention
from mailmind.bootstrap.mailbox import store

LUNCH_SUBJECT = "Lunch on Thursday?"
_uid = itertools.count(9000)


def listed(app, account=w.ADA, folder="INBOX"):
    return app.get(f"/api/messages?account={account}&folder={folder}&limit=500&group=thread").json["items"]


def lunch_row(app):
    return next(i for i in listed(app) if LUNCH_SUBJECT in i["subject"])


def opened(app, row):
    return app.get(f"/api/thread?account={row['account']}&thread_id={quote(row['thread_id'])}").json


def execute(sql, *args):
    c = store.conn()
    c.execute(sql, args)
    c.commit()
    c.close()


def copy_of(row, folder, message_id, account=w.ADA):
    """One more row of `row`'s conversation, in a folder of its own."""
    c = store.conn()
    pk = store.upsert_message(c, {"account": account, "kind": "imap", "folder": folder, "uid": next(_uid), "message_id": message_id,
                                  "gmail_thread": None, "from_addr": "lin@example.test", "from_name": "Lin", "subject": LUNCH_SUBJECT,
                                  "date_iso": None, "ts": 1, "snippet": "", "bucket": "personal/protected", "is_junk": 0})
    c.execute("UPDATE messages SET thread_id=? WHERE pk=?", (row["thread_id"], pk))
    c.commit()
    c.close()
    return pk


def test_a_reply_in_sent_counts_though_the_list_holds_two_of_three(app):
    row = lunch_row(app)
    assert (row["count"], row["thread_count"]) == (2, 3)
    assert opened(app, row)["count"] == 3


def test_every_conversation_row_says_what_opening_it_shows(app):
    for account in (w.ADA, w.BEA, w.CY):
        for row in listed(app, account):
            if row["thread_id"]:
                assert row["thread_count"] == opened(app, row)["count"], row["subject"]


def test_the_list_of_every_inbox_says_the_same(app):
    rows = [r for r in app.get("/api/inboxes?limit=500&group=thread").json["items"] if r["thread_id"]]
    assert rows
    for row in rows:
        assert row["thread_count"] == opened(app, row)["count"], row["subject"]


def test_a_copy_of_one_message_in_another_folder_counts_once(app):
    row = lunch_row(app)
    message_id = store.conn().execute("SELECT message_id FROM messages WHERE message_key=? AND folder=?", (row["key"], row["folder"])).fetchone()[0]
    copy_of(row, "All Mail", message_id)
    assert lunch_row(app)["thread_count"] == opened(app, row)["count"] == 3


def test_copies_with_no_message_id_each_count(app):
    row = lunch_row(app)
    copy_of(row, "Archive", "")
    copy_of(row, "Archive", "  ")
    assert lunch_row(app)["thread_count"] == opened(app, row)["count"] == 5


def test_a_message_on_its_way_out_has_left_both(app):
    row = lunch_row(app)
    execute("UPDATE messages SET pending_removal=1 WHERE account=? AND thread_id=? AND folder='Sent'", row["account"], row["thread_id"])
    assert lunch_row(app)["thread_count"] == opened(app, row)["count"] == 2


def test_a_row_not_yet_threaded_has_only_the_list_to_go_by(app):
    row = lunch_row(app)
    execute("UPDATE messages SET thread_id=NULL WHERE account=? AND thread_id=?", row["account"], row["thread_id"])
    row = lunch_row(app)
    assert row["thread_id"] is None and row["thread_count"] == row["count"]


def test_a_muted_conversation_says_what_opening_it_shows(app):
    row = lunch_row(app)
    message_id = store.conn().execute("SELECT message_id FROM messages WHERE message_key=? AND folder=?", (row["key"], row["folder"])).fetchone()[0]
    copy_of(row, "All Mail", message_id)
    assert attention.muting.mute({"account": row["account"], "thread_id": row["thread_id"]})
    muted = next(i for i in listed(app, folder="__muted__") if i["thread_id"] == row["thread_id"])
    assert muted["count"] == opened(app, row)["count"] == 3


def test_a_conversation_waiting_on_a_reply_says_what_opening_it_shows(app):
    row = lunch_row(app)
    message_id = store.conn().execute("SELECT message_id FROM messages WHERE message_key=? AND folder=?", (row["key"], row["folder"])).fetchone()[0]
    copy_of(row, "All Mail", message_id)
    execute("INSERT INTO followups (account, thread_id, kind, state, first_sent_ts, due_ts, created_at) VALUES (?,?,?,?,?,?,?)",
            row["account"], row["thread_id"], "sent", "due", 1, 1, "2026-11-01T00:00:00+00:00")
    assert next(i for i in listed(app, folder="__waiting__") if i["thread_id"] == row["thread_id"])["count"] == opened(app, row)["count"] == 3


def test_a_reply_later_conversation_says_what_opening_it_shows(app):
    row = lunch_row(app)
    message_id = store.conn().execute("SELECT message_id FROM messages WHERE message_key=? AND folder=?", (row["key"], row["folder"])).fetchone()[0]
    copy_of(row, "All Mail", message_id)
    execute("INSERT INTO followups (account, thread_id, kind, state, first_sent_ts, due_ts, created_at) VALUES (?,?,?,?,?,?,?)",
            row["account"], row["thread_id"], "replylater", "due", 1, 1, "2026-11-01T00:00:00+00:00")
    assert next(i for i in listed(app, folder="__replylater__") if i["thread_id"] == row["thread_id"])["count"] == opened(app, row)["count"] == 3
