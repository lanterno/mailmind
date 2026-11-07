"""One change to Compose's tables: the write lock is taken up front, and the change stands or goes whole."""
import sqlite3

import pytest

from mailmind.bootstrap.compose import book, drafts
from mailmind.platform import settings

STAMP = "2026-11-02T12:00:00+00:00"
DRAFT = {"id": None, "account": "ada@example.test", "folder": None, "uid": None, "message_id": "<d@example.test>",
         "uidvalidity": None, "in_reply_to_pk": None, "mode": "new", "subject": "A draft", "created_at": STAMP,
         "updated_at": STAMP}


def stored(did):
    c = book.connect()
    try:
        return book.draft_get(c, did)
    finally:
        c.close()


def test_a_block_that_ends_is_committed(data_dir):
    with book.transaction() as c:
        did = book.draft_upsert(c, DRAFT)
    assert stored(did)["subject"] == "A draft"


def test_a_block_that_raises_leaves_nothing(data_dir):
    with pytest.raises(ValueError):
        with book.transaction() as c:
            did = book.draft_upsert(c, DRAFT)
            raise ValueError
    assert stored(did) is None


def test_a_block_that_returns_early_keeps_what_it_wrote_and_closes(data_dir):
    def early():
        with book.transaction() as c:
            return c, book.draft_upsert(c, DRAFT)
    c, did = early()
    assert stored(did) is not None
    with pytest.raises(sqlite3.ProgrammingError):
        c.execute("SELECT 1")


def test_the_write_lock_is_taken_before_the_block_runs(data_dir):
    other = sqlite3.connect(settings.current().db, timeout=0)
    try:
        with book.transaction():
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE")
        other.execute("BEGIN IMMEDIATE")               # and let go when it ends
        other.rollback()
    finally:
        other.close()


def test_discarding_a_draft_with_no_row_answers_as_gone(data_dir):
    assert drafts.save({"delete": True, "draft_id": 12345}) == ({"ok": True, "deleted": True}, 200)
