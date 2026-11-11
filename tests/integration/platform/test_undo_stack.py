"""The undo stack, which lives in the database (undo_entries, migration 25) so Undo outlives the
process: what a push adds, how an undo group gathers its members, and what the stack keeps.
tests/e2e/test_front_door.py undoes across a restart; the routes' undo is in test_http_mail.py and
characterization/mailbox/test_undo.py."""
from datetime import timedelta


import problems
from mailmind.bootstrap import mailbox


def move(n):
    return {"kind": "move", "account": "ada@example.test", "src_folder": "INBOX", "dest_folder": "Archive",
            "message_id": f"<m{n}@example.test>", "label": "Archived"}


def ids(stack):
    return [[c["message_id"] for c in e["children"]] if e["kind"] == "bulk" else e["message_id"] for e in stack]


def test_an_entry_a_day_old_can_no_longer_be_undone(app, world):
    """Whichever comes first, of 50 groups and 24 hours: Undo finds nothing, and asks no server."""
    mailbox.undo.push(move(1))
    app.clock.shift(timedelta(hours=23, minutes=59))
    assert ids(mailbox.undo.entries()) == ["<m1@example.test>"]
    app.clock.shift(timedelta(minutes=2))
    assert mailbox.undo.entries() == []
    r = app.post("/api/undo", {})
    assert (r.status, r.json) == (404, problems.not_found(empty=True))
