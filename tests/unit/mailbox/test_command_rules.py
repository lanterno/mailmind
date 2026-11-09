"""What a command's row says, what reverses it and what the tray shows (mailmind.mailbox.domain.command), and which message a
try acts on (mailmind.mailbox.domain.try_target). Values in and values out: test_command_path.py and
test_queue_state_machine.py run them through the path."""
import pytest

from mailmind.mailbox.domain import command, try_target
from mailmind.mailbox.domain.destination import Destination


def message(**over):
    return {"pending_removal": None, "folder": "INBOX", "account": "ada@example.test", "uid": 5, "seen": 1, "subject": "Lunch",
            "thread_id": "rf:1", "from_name": "Ada", "from_addr": "ada@example.test", **over}


# -- which message a try acts on ---------------------------------------------

def target(uid=5, epoch=7, uidvalidity=7, first_try=True, has_message_id=True):
    return try_target.of_try(uid=uid, epoch=epoch, uidvalidity=uidvalidity, first_try=first_try, has_message_id=has_message_id)


def test_a_first_try_acts_on_the_uid_seen_and_searches_for_nothing():
    assert target() == try_target.Target(by_message_id=False, uid=5)


def test_a_later_try_asks_by_message_id_since_an_earlier_one_may_have_moved_it():
    assert target(first_try=False) == try_target.Target(by_message_id=True, uid=5)


def test_a_first_try_in_another_epoch_never_believes_the_uid():
    assert target(uidvalidity=9) == try_target.Target(by_message_id=True, uid=None)


def test_with_no_message_id_a_uid_from_another_epoch_names_nothing():
    assert target(uidvalidity=9, has_message_id=False) == try_target.Target(by_message_id=False, uid=None)


def test_with_no_uid_kept_the_message_id_says():
    assert target(uid=None) == try_target.Target(by_message_id=True, uid=None)


@pytest.mark.parametrize("epoch, uidvalidity", [(7, 7), (None, 7), (7, None), (None, None)])
def test_an_epoch_either_side_doesnt_say_counts_as_the_same(epoch, uidvalidity):
    assert try_target.same_epoch(epoch, uidvalidity)


def test_another_epoch_is_not_the_same():
    assert not try_target.same_epoch(7, 9)


@pytest.mark.parametrize("just_accepted, begun, first", [(True, 1, True), (False, 0, True), (False, 1, False)])
def test_a_try_is_a_first_unless_an_earlier_one_may_have_reached_the_server(just_accepted, begun, first):
    assert try_target.is_first_try(just_accepted, begun) is first


# -- accepting a move --------------------------------------------------------

@pytest.mark.parametrize("given", ["m1", "x" * 128])
def test_a_clients_id_names_its_request(given):
    assert command.names_a_request(given)


@pytest.mark.parametrize("given", ["", "x" * 129, None, 7])
def test_anything_else_makes_a_request_its_own(given):
    assert not command.names_a_request(given)


def test_a_key_goes_by_the_message_id_and_else_the_row():
    assert command.idempotency_key("trash", "a@example.test", "<m@example.test>", 3, "r") == "trash:a@example.test:<m@example.test>:r"
    assert command.idempotency_key("trash", "a@example.test", "", 3, "r") == "trash:a@example.test:3:r"


def test_a_message_can_be_asked_to_move():
    assert command.takes_a_move(message(), None)
    assert command.takes_a_move(message(), "Receipts")


@pytest.mark.parametrize("row, to", [(None, None), (message(pending_removal=4), None), (message(), "INBOX")],
                         ids=["gone", "already-going", "already-there"])
def test_a_message_that_cant_be_moved_is_not_taken(row, to):
    assert not command.takes_a_move(row, to)


def test_what_a_move_keeps_of_its_message():
    assert command.move_params(3, message(seen=0), 7) == {"pk": 3, "uid": 5, "epoch": 7, "seen": False, "subject": "Lunch", "thread_id": "rf:1"}


def test_an_undo_entry_says_where_it_went_and_how_to_show_it():
    entry = command.undo_entry("junk", 9, message(), "<m@example.test>", Destination("Spam"))
    assert entry == {"kind": "move", "command": 9, "account": "ada@example.test", "src_folder": "INBOX", "dest_folder": "Spam",
                     "message_id": "<m@example.test>", "label": "Marked junk", "subject": "Lunch", "who": "Ada", "thread_id": "rf:1"}


def test_a_gmail_archive_is_undone_as_one_and_names_no_folder():
    entry = command.undo_entry("archive", 9, message(), "<m@example.test>", Destination(drop_inbox_label=True))
    assert entry["kind"] == "gmail_archive" and entry["dest_folder"] == "" and entry["label"] == "Archived"


def test_an_undo_entry_shows_the_address_with_no_name():
    assert command.undo_entry("move", 9, message(from_name=""), "<m@example.test>", Destination("X"))["who"] == "ada@example.test"


# -- what reverses it, and the tray -------------------------------------------

ROW = {"account": "a@example.test", "message_id": "<m@example.test>", "src_folder": "INBOX"}


@pytest.mark.parametrize("kind", ["move", "gmail_archive"])
def test_the_entry_of_this_move_reverses_it(kind):
    assert command.reverses({"kind": kind, **ROW}, ROW)


@pytest.mark.parametrize("other", [{"kind": "snooze"}, {"account": "b@example.test"}, {"message_id": "<n@example.test>"}, {"src_folder": "Sent"}],
                         ids=["another-kind", "another-account", "another-message", "another-folder"])
def test_no_other_entry_does(other):
    assert not command.reverses({"kind": "move", **ROW, **other}, ROW)


def test_a_row_given_up_on_says_why():
    assert command.why_given_up(command.REMOVED) == "account removed"
    assert command.why_given_up(command.REFUSED) == command.REFUSED


def test_the_tray_counts_a_try_as_waiting():
    assert command.tray_counts({"queued": 2, "running": 1, "error": 4}) == {"waiting": 3, "stuck": 4}
    assert command.tray_counts({}) == {"waiting": 0, "stuck": 0}


def test_the_tray_shows_a_change_as_the_page_reads_it():
    row = {"id": 1, "account": "a@example.test", "kind": "flag", "src_folder": "INBOX", "dest": None, "state": "error", "attempts": 3,
           "last_error": "refused", "next_attempt_at": 0, "created_at": "2026-11-02T12:00:00+00:00"}
    assert command.tray_item(row, {"flags": {"seen": True}}, "Lunch") == {
        "id": 1, "account": "a@example.test", "op": "flag", "folder": "INBOX", "dest": None, "flags": {"seen": True}, "state": "error",
        "attempts": 3, "error": "refused", "subject": "Lunch", "next_attempt_at": 0, "created_at": "2026-11-02T12:00:00+00:00"}
