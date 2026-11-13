"""A list reads a folder in the order it shows it (m51's `idx_msg_listed`), so its first page is the first rows read and no
list sorts a whole folder before it can answer."""
import re

import pytest

from mailmind.mailbox.adapters.sqlite import lists

A, B = "a@example.test", "b@example.test"


@pytest.fixture
def db(data_dir):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    rows = [(acct, "INBOX" if n % 3 else "Archive", n, f"<{n}@example.test>", 1000 - n, None if n % 7 else 1)
            for n in range(60) for acct in (A, B)]
    c.executemany("INSERT INTO messages(account, kind, folder, uid, message_id, ts, pending_removal) "
                  "VALUES (?, 'imap', ?, ?, ?, ?, ?)", rows)
    c.execute("UPDATE messages SET message_key = pk, thread_id = 'rf:' || pk")
    c.commit()
    yield c
    c.close()


def plans(c, call):
    """What SQLite does for each statement `call` runs."""
    statements = []
    c.set_trace_callback(statements.append)
    try:
        call()
    finally:
        c.set_trace_callback(None)
    return [[row[3] for row in c.execute("EXPLAIN QUERY PLAN " + sql)]
            for sql in statements if re.match(r"\s*(WITH|SELECT)", sql)]


def test_the_inboxes_together_are_read_from_the_index_in_their_order(db):
    [plan] = plans(db, lambda: lists.inboxes_rows(db, [A, B], 50, 0))
    assert plan[0] == "SEARCH m USING INDEX idx_msg_listed (folder=?)"
    assert not any("TEMP B-TREE" in step for step in plan)


def test_one_folder_is_read_in_its_order_too(db):
    [plan] = plans(db, lambda: lists.folder_rows(db, A, "INBOX", 50, 0))
    assert plan[0].startswith("SEARCH m USING INDEX")
    assert not any("TEMP B-TREE" in step for step in plan)


def test_the_index_leaves_out_what_is_on_its_way_out_and_the_order_is_the_lists(db):
    listed = lists.inboxes_rows(db, [A, B], 500, 0)
    assert all(r["folder"] == "INBOX" for r in listed)
    gone = {r[0] for r in db.execute("SELECT pk FROM messages WHERE pending_removal IS NOT NULL")}
    assert gone and not gone & {r["pk"] for r in listed}
    assert [(r["ts"], r["pk"]) for r in listed] == sorted(((r["ts"], r["pk"]) for r in listed), reverse=True)
