"""An idempotent insert that loses a race to another writer returns the winner's row, in the outbox and the command log."""
from mailmind.bootstrap.compose import book
from stored_rows import A


class _LosesTheRace:
    """A connection whose first SELECT finds nothing: another writer stores the same key between
    the caller's SELECT and its INSERT."""

    def __init__(self, c):
        self.c, self.first = c, True

    def execute(self, sql, *args):
        if self.first and sql.lstrip().upper().startswith("SELECT"):
            self.first = False
            return self.c.execute("SELECT 1 WHERE 0")
        return self.c.execute(sql, *args)


def test_hwf_an_outbox_insert_that_loses_a_race_returns_the_winners_row(db):
    row = {"account": A, "idempotency_key": "send:k1", "message_id": "<o@example.test>",
           "envelope": "[]", "recipients": "{}", "subject": "s", "raw": b"x", "next_attempt_at": 0,
           "draft_id": None, "created_at": "now"}
    oid, created = book.outbox_insert(db, row)
    assert created and book.outbox_insert(_LosesTheRace(db), row) == (oid, False)
    assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1


def test_hwf_a_queued_mutation_insert_that_loses_a_race_returns_the_winners_row(db):
    from mailmind.bootstrap.mailbox import store
    row = {"account": A, "idempotency_key": "trash:k1", "kind": "trash", "message_id": "<m@example.test>",
           "src_folder": "INBOX", "dest": "Trash", "params": "{}", "next_attempt_at": 0, "created_at": "now"}
    mid, created = store.command_insert(db, row)
    assert created and store.command_insert(_LosesTheRace(db), row) == (mid, False)
    assert db.execute("SELECT count(*) FROM mail_commands").fetchone()[0] == 1
