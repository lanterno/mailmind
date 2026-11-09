"""A message's flags as the server says them, as the columns hold them, and what a read of the server's may write
(mailmind.mailbox.domain.flags). Values in and values out: test_sync_state.py runs a read against the fake."""
import pytest

from mailmind.mailbox.domain import flags


def row(pk=1, uid=10, message_id="<a@example.test>", pending_removal=None, **columns):
    return {"pk": pk, "uid": uid, "message_id": message_id, "pending_removal": pending_removal,
            **dict.fromkeys(flags.COLUMNS, 0), **columns}


def test_a_servers_flags_become_columns_whatever_case_it_writes():
    assert flags.columns_of(["\\SEEN", "\\flagged", "$Forwarded"]) == {
        "seen": 1, "flagged": 1, "answered": 0, "draft": 0, "deleted": 0}


def test_no_flags_is_every_column_off():
    assert flags.columns_of([]) == dict.fromkeys(flags.COLUMNS, 0)


def test_every_column_has_the_servers_word_for_it():
    assert set(flags.IMAP_NAME) == set(flags.COLUMNS)
    for column, name in flags.IMAP_NAME.items():
        assert flags.columns_of([name])[column] == 1


@pytest.mark.parametrize("said, seen", [(["\\Seen"], True), (["\\SEEN", "\\Flagged"], True), (["\\Flagged"], False), ([], False)])
def test_whether_a_server_says_a_message_was_read(said, seen):
    assert flags.has_seen(said) is seen


def test_a_change_is_held_as_ones_and_noughts():
    assert flags.as_columns({"seen": True, "flagged": False, "answered": 1}) == {"seen": 1, "flagged": 0, "answered": 1}


def test_what_a_change_replaces_is_what_the_row_holds_of_those_flags_alone():
    assert flags.held_now(row(seen=1), ["seen", "flagged"]) == {"seen": True, "flagged": False}


def test_a_filter_says_only_what_the_row_doesnt_already_hold():
    assert flags.still_to_say({"seen": True, "flagged": True}, row(seen=1)) == {"flagged": True}
    assert flags.still_to_say({"seen": True}, row(seen=1)) == {}


def test_a_flag_read_writes_what_the_server_says_otherwise():
    r = row()
    read = flags.read_against([r], {10: ["\\Seen"]}, set())
    assert read.changed == [(r, {**dict.fromkeys(flags.COLUMNS, 0), "seen": 1})]
    assert not read.held_back


def test_a_row_the_server_agrees_with_is_not_written():
    assert flags.read_against([row(seen=1)], {10: ["\\Seen"]}, set()).changed == []


def test_a_uid_the_server_didnt_name_changes_nothing():
    read = flags.read_against([row(uid=11, seen=1)], {10: []}, set())
    assert read.changed == [] and not read.held_back


def test_a_row_on_its_way_out_keeps_its_flags_and_holds_the_mark_back():
    read = flags.read_against([row(pending_removal=7)], {10: ["\\Seen"]}, set())
    assert read.changed == [] and read.held_back


def test_a_row_with_a_flag_change_still_owed_keeps_its_flags_and_holds_the_mark_back():
    read = flags.read_against([row()], {10: ["\\Seen"]}, {"<a@example.test>"})
    assert read.changed == [] and read.held_back


def test_one_held_back_row_leaves_the_others_to_be_written():
    held, free = row(pk=1, uid=10, pending_removal=7), row(pk=2, uid=11, message_id="<b@example.test>")
    read = flags.read_against([held, free], {10: ["\\Seen"], 11: ["\\Seen"]}, set())
    assert [r["pk"] for r, _ in read.changed] == [2] and read.held_back
