"""Moving many messages out of the INBOX, checked (mailmind.mailbox.application.bulk_moves.move_verified): MOVE_BATCH at
a time, one message at a time where a batch is refused, then a look at the INBOX and one more try for what stayed. What
stays after that comes back as a straggler; a lost connection stops it all."""
import imaplib
from types import SimpleNamespace

import pytest

from mailmind.mailbox.application import bulk_moves
from mailmind.mailbox.application.bulk_moves import move_verified


class _Inbox:
    """A server's INBOX. `refused` UIDs make any MOVE naming them a NO; `ignored` ones the server answers OK for and keeps
    for that many MOVEs."""

    def __init__(self, uids, refused=(), ignored=None):
        self.uids_in = {str(u) for u in uids}
        self.refused = {str(u) for u in refused}
        self.ignored = dict(ignored or {})
        self.moves = []
        self.selected = []

    def select(self, folder, readonly=True):
        self.selected.append((folder, readonly))

    def uids(self, *criteria):
        assert criteria == ("ALL",)
        return sorted(int(u) for u in self.uids_in)

    def move(self, uids, dest):
        self.moves.append((list(uids), dest))
        if self.refused & set(uids):
            raise imaplib.IMAP4.error("MOVE refused")
        for u in uids:
            if self.ignored.get(u):
                self.ignored[u] -= 1
            else:
                self.uids_in.discard(u)


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(bulk_moves, "time", SimpleNamespace(sleep=lambda seconds: None))


def uids(n, start=1):
    return [str(u) for u in range(start, start + n)]


def test_all_go_in_one_move_a_batch_and_none_is_left(monkeypatch):
    monkeypatch.setattr(bulk_moves, "MOVE_BATCH", 3)
    inbox = _Inbox(range(1, 8))
    assert move_verified(inbox, uids(7), "Trash") == (7, [])
    assert inbox.moves == [(uids(3), "Trash"), (uids(3, 4), "Trash"), (["7"], "Trash")]
    assert inbox.selected[0] == ("INBOX", False)


def test_a_refused_batch_goes_one_at_a_time_and_one_bad_uid_sinks_no_other():
    inbox = _Inbox(range(1, 4), refused={2})
    assert move_verified(inbox, uids(3), "Trash") == (2, ["2"])
    assert inbox.moves == [(uids(3), "Trash"), (["1"], "Trash"), (["2"], "Trash"), (["3"], "Trash"), (["2"], "Trash")]
    assert inbox.uids_in == {"2"}


def test_one_still_there_after_the_pass_is_tried_once_more():
    inbox = _Inbox(range(1, 4), ignored={"2": 1})
    assert move_verified(inbox, uids(3), "Trash") == (3, [])
    assert inbox.moves == [(uids(3), "Trash"), (["2"], "Trash")]
    assert inbox.selected == [("INBOX", False), ("INBOX", True), ("INBOX", False), ("INBOX", True)]


def test_one_still_there_after_its_second_try_is_a_straggler():
    inbox = _Inbox(range(1, 4), ignored={"3": 2})
    assert move_verified(inbox, uids(3), "Trash") == (2, ["3"])


def test_mail_in_the_inbox_it_wasnt_asked_to_move_stays_out_of_it():
    inbox = _Inbox(range(1, 6), ignored={"2": 1})
    assert move_verified(inbox, ["1", "2"], "Trash") == (2, [])
    assert inbox.uids_in == {"3", "4", "5"} and inbox.moves[-1] == (["2"], "Trash")


def test_a_lost_connection_stops_it_all():
    class _Gone(_Inbox):
        def move(self, uids, dest):
            raise imaplib.IMAP4.abort("the connection went")
    with pytest.raises(imaplib.IMAP4.abort):
        move_verified(_Gone(range(1, 3)), uids(2), "Trash")


def test_a_lost_connection_one_at_a_time_stops_it_all_too():
    class _GoesMidway(_Inbox):
        def move(self, uids, dest):
            if len(uids) == 1:
                raise imaplib.IMAP4.abort("the connection went")
            super().move(uids, dest)
    with pytest.raises(imaplib.IMAP4.abort):
        move_verified(_GoesMidway(range(1, 3), refused={1}), uids(2), "Trash")
