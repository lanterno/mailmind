"""The undo stack, which lives in the database (undo_entries, migration 25) so Undo outlives the
process: what a push adds, how an undo group gathers its members, and what the stack keeps.
tests/test_front_door.py undoes across a restart; the routes' undo is in test_http_mail.py and
test_hwf_http.py."""
from mailmind.bootstrap import mailbox


def move(n):
    return {"kind": "move", "account": "ada@example.test", "src_folder": "INBOX", "dest_folder": "Archive",
            "message_id": f"<m{n}@example.test>", "label": "Archived"}


def ids(stack):
    return [[c["message_id"] for c in e["children"]] if e["kind"] == "bulk" else e["message_id"] for e in stack]


def test_a_group_gathers_its_members_only_while_it_is_on_top(data_dir):
    mailbox.undo.push(move(1), "g")
    mailbox.undo.push(move(2), "g")
    mailbox.undo.push(move(3))
    mailbox.undo.push(move(4), "g")                                # g is no longer on top: a new entry
    assert ids(mailbox.undo.entries()) == [["<m1@example.test>", "<m2@example.test>"], "<m3@example.test>",
                                          ["<m4@example.test>"]]
    assert mailbox.undo.entries()[0]["label"] == "Archived"


def test_the_stack_keeps_its_newest_entries(data_dir, monkeypatch):
    monkeypatch.setattr(mailbox.undo, "UNDO_MAX", 3)
    for n in range(5):
        mailbox.undo.push(move(n))
    assert ids(mailbox.undo.entries()) == ["<m2@example.test>", "<m3@example.test>", "<m4@example.test>"]


def test_nothing_to_reverse_pushes_nothing(data_dir):
    mailbox.undo.push(None)
    mailbox.undo.push({}, "g")
    assert mailbox.undo.entries() == []


def test_an_undo_that_fails_puts_its_entry_back_on_top(data_dir, monkeypatch):
    mailbox.undo.push(move(1))
    mailbox.undo.push(move(2))
    monkeypatch.setattr(mailbox.undo, "_reverse_one", lambda d: (False, d["account"], None))
    assert mailbox.undo.undo_last()[0]["ok"] is False
    assert ids(mailbox.undo.entries()) == ["<m1@example.test>", "<m2@example.test>"]
