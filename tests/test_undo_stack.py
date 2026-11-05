"""The undo stack, which lives in the database (undo_entries, migration 25) so Undo outlives the
process: what a push adds, how an undo group gathers its members, and what the stack keeps.
tests/test_front_door.py undoes across a restart; the routes' undo is in test_http_mail.py and
test_hwf_http.py."""


def move(n):
    return {"kind": "move", "account": "ada@example.test", "src_folder": "INBOX", "dest_folder": "Archive",
            "message_id": f"<m{n}@example.test>", "label": "Archived"}


def ids(stack):
    return [[c["message_id"] for c in e["children"]] if e["kind"] == "bulk" else e["message_id"] for e in stack]


def test_a_group_gathers_its_members_only_while_it_is_on_top(data_dir):
    import server
    server.undo_push(move(1), "g")
    server.undo_push(move(2), "g")
    server.undo_push(move(3))
    server.undo_push(move(4), "g")                                # g is no longer on top: a new entry
    assert ids(server.undo_entries()) == [["<m1@example.test>", "<m2@example.test>"], "<m3@example.test>",
                                          ["<m4@example.test>"]]
    assert server.undo_entries()[0]["label"] == "Archived"


def test_the_stack_keeps_its_newest_entries(data_dir, monkeypatch):
    import server
    monkeypatch.setattr(server, "UNDO_MAX", 3)
    for n in range(5):
        server.undo_push(move(n))
    assert ids(server.undo_entries()) == ["<m2@example.test>", "<m3@example.test>", "<m4@example.test>"]


def test_nothing_to_reverse_pushes_nothing(data_dir):
    import server
    server.undo_push(None)
    server.undo_push({}, "g")
    assert server.undo_entries() == []


def test_an_undo_that_fails_puts_its_entry_back_on_top(data_dir, monkeypatch):
    import server
    server.undo_push(move(1))
    server.undo_push(move(2))
    monkeypatch.setattr(server, "_reverse_one", lambda d: (False, d["account"], None))
    assert server.do_undo()[0]["ok"] is False
    assert ids(server.undo_entries()) == ["<m1@example.test>", "<m2@example.test>"]
