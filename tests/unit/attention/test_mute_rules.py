"""What a mute holds to, with no database, no clock and no I/O."""
from mailmind.attention.domain import mute

ADA = "ada@example.test"


def message(key, folder="INBOX"):
    return {"message_key": key, "folder": folder}


def test_a_repeat_mute_is_a_no_op():
    assert mute.plan(True, ADA, "T", [message(1)], message(2)) is None


def test_a_mute_marks_the_unread_read_and_remembers_them_by_key_and_folder():
    todo = mute.plan(False, ADA, "T", [message(1), message(2, "Archive")], message(3))
    assert todo.mark_read == [message(1), message(2, "Archive")]
    assert todo.undo == {"kind": "mute", "account": ADA, "thread_id": "T", "unread": [[1, "INBOX"], [2, "Archive"]],
                         "label": "Muted", "member": [3, "INBOX"]}


def test_a_thread_read_already_has_nothing_to_mark_and_undo():
    todo = mute.plan(False, ADA, "T", [], message(3))
    assert list(todo.mark_read) == [] and todo.undo["unread"] == []


def test_a_thread_with_no_member_left_names_none():
    assert mute.plan(False, ADA, "T", [], None).undo["member"] is None


def test_undo_unmutes_the_thread_it_named_and_the_one_it_has_become():
    assert mute.threads_to_unmute({"thread_id": "T"}, ["U"]) == {"T", "U"}
    assert mute.threads_to_unmute({"thread_id": "T"}, ["T"]) == {"T"}
    assert mute.threads_to_unmute({"thread_id": "T"}, []) == {"T"}


def test_undo_marks_unread_only_what_the_mute_marked_read():
    assert mute.marked_read(mute.plan(False, ADA, "T", [message(1)], message(1)).undo) == [[1, "INBOX"]]
    assert mute.marked_read({}) == []


def test_only_an_old_entry_has_prev_seen_to_restore():
    assert mute.seen_to_restore({"prev_seen": [[7, 1], [8, 0], [9, None]]}) == [(7, 1), (8, 0), (9, 0)]
    assert mute.seen_to_restore(mute.plan(False, ADA, "T", [message(1)], None).undo) == []
