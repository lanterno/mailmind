"""A snooze names at most `BULK_CAP` copies. One naming more is the schema's 400 and snoozes none of them, never the first 500 of
them with the rest left as they were, since the page has already taken every marked row off the list. The page sends more as
more requests, under one undo group."""
import problems
from mailmind.attention.domain.snooze import BULK_CAP
from mailmind.bootstrap import mailbox
from mailmind.bootstrap.mailbox import store

RECEIPT = "ada-inbox-05-receipt.eml"
FIELDS = ("account", "kind", "folder", "gmail_thread", "from_addr", "from_name", "date_iso", "ts", "snippet", "bucket", "is_junk")


def in_the_inbox(world, n):
    """`n` more messages in Ada's Inbox, each one of its own, stored as ingest stores one: the copies a request names them by."""
    like = world.row(RECEIPT)
    c = store.conn()
    try:
        copies = []
        for i in range(n):
            uid = 50_000 + i
            pk = store.upsert_message(c, {**{k: like[k] for k in FIELDS}, "uid": uid, "message_id": f"<many-{i}@example.test>",
                                          "subject": f"Note {i}"})
            key = c.execute("SELECT message_key FROM messages WHERE pk=?", (pk,)).fetchone()[0]
            copies.append({"key": key, "folder": "INBOX", "uid": uid})
        c.commit()
        return copies
    finally:
        c.close()


def snoozed():
    c = store.conn()
    try:
        return c.execute("SELECT COUNT(*) FROM snoozes").fetchone()[0]
    finally:
        c.close()


def test_one_more_than_a_request_may_name_is_the_schemas_400_and_snoozes_none(app, world):
    r = app.post("/api/snooze", {"copies": in_the_inbox(world, BULK_CAP + 1), "preset": "tomorrow", "undo_group": "ug-1"})
    assert (r.status, r.json) == (400, problems.BAD_REQUEST)
    assert snoozed() == 0 and mailbox.undo.entries() == []


def test_as_many_as_a_request_may_name_all_snooze_and_one_undo_wakes_them(app, world):
    copies = in_the_inbox(world, BULK_CAP)
    r = app.post("/api/snooze", {"copies": copies, "preset": "tomorrow", "undo_group": "ug-1"}).json
    assert BULK_CAP == 500 and (r["ok"], r["count"]) == (True, 500)
    assert snoozed() == 500 and len(mailbox.undo.entries()) == 1
    assert app.post("/api/undo", {}).json["ok"]
    assert snoozed() == 0


def test_an_unsnooze_naming_too_many_wakes_none(app, world):
    copies = in_the_inbox(world, BULK_CAP + 1)
    assert app.post("/api/snooze", {"copies": copies[:BULK_CAP], "preset": "tomorrow"}).json["count"] == BULK_CAP
    r = app.post("/api/unsnooze", {"copies": copies})
    assert (r.status, r.json) == (400, problems.BAD_REQUEST)
    assert snoozed() == BULK_CAP


def test_more_than_one_request_snoozes_as_more_requests_under_one_undo_group(app, world):
    copies = in_the_inbox(world, BULK_CAP + 1)
    for at in range(0, len(copies), BULK_CAP):
        assert app.post("/api/snooze", {"copies": copies[at:at + BULK_CAP], "preset": "tomorrow", "undo_group": "ug-1"}).json["ok"]
    assert snoozed() == BULK_CAP + 1 and len(mailbox.undo.entries()) == 1
    u = app.post("/api/undo", {}).json
    assert (u["ok"], u["reversed"], u["failed"]) == (True, 2, 0)
    assert snoozed() == 0
