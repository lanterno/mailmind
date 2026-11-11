"""Marked rows filed as one request (mailmind.mailbox.application.filing.Filing.file_many), on a stand-in for the store and the
command path: which messages each row stands for, every one accepted before any is tried, what each try comes to, and how a
click past FILE_WAIT leaves the rest to the worker. The rows through the fake server, Undo, a busy gate and what the route
refuses are the integration half's."""
import contextlib

from mailmind.mailbox.application.filing import Filing
from mailmind.mailbox.domain import command

ADA = "ada@example.test"


class _Mail:
    """What Filing reads of Ada's rows (pk -> its key, folder, uid and thread) and asks of the command path. `outcome` says
    what a try at a message comes to; `log` holds each step in order."""
    FILE_WAIT = 2.5

    def __init__(self, *rows):
        self.rows = {r["pk"]: {"account": ADA, "folder": "INBOX", "uid": r["pk"], "message_key": 100 + r["pk"], "thread_id": None,
                               **r} for r in rows}
        self.outcome, self.log = {}, []

    def copy(self, pk, whole=False):
        r = self.rows[pk]
        return {"key": r["message_key"], "folder": r["folder"], "uid": r["uid"], "whole": whole}

    # what Filing reads of the store
    def copy_pk(self, c, key, folder, uid=None):
        return next((p for p, r in self.rows.items() if (r["message_key"], r["folder"]) == (key, folder)
                     and uid in (None, r["uid"])), None)

    def get_message(self, c, pk):
        return self.rows.get(pk)

    def thread_pks_in(self, c, account, folder, thread_id):
        return [p for p, r in self.rows.items() if (r["account"], r["folder"], r["thread_id"]) == (account, folder, thread_id)]

    def names_of(self, c, pks):
        return {p: (self.rows[p]["message_key"], self.rows[p]["folder"], self.rows[p]["uid"]) for p in pks if p in self.rows}

    # what it asks of the command path
    def accept_move_within(self, c, kind, pk, mutation_id, push_undo=None, to=None):
        self.log.append(("accept", pk))
        push_undo(c, {"pk": pk})
        return command.Accepted(pk, ADA, True, True, {"id": pk})

    def submit(self, accepted):
        self.log.append(("try", accepted.id))
        return self.outcome.get(accepted.id, command.APPLIED)

    def leave_to_worker(self, accepted):
        self.log.append(("left", accepted.id))
        return command.WAITING

    def kick(self):
        pass


class _Uow:
    @contextlib.contextmanager
    def read(self):
        yield "c"

    write = read


class _Aside:
    """Undo, the counts and the rail: what filing tells them goes nowhere here."""
    def push_within(self, c, entry, group=None):
        pass

    def invalidate(self, *account):
        pass


def file_many(mail, verb, *rows):
    return Filing(_Uow(), mail, None, mail, None, _Aside(), _Aside(), _Aside(), None).file_many({"verb": verb, "rows": list(rows)})


def lunch():
    """A conversation of two in the Inbox, your reply to it in Sent, and a receipt on its own."""
    return _Mail({"pk": 1, "thread_id": "rf:lunch"}, {"pk": 2, "thread_id": "rf:lunch"},
                 {"pk": 3, "thread_id": "rf:lunch", "folder": "Sent"}, {"pk": 5, "thread_id": "rf:receipt"})


def accepted(mail):
    return [pk for step, pk in mail.log if step == "accept"]


def test_a_whole_row_files_its_conversation_in_its_folder():
    mail = lunch()
    a = file_many(mail, "trash", mail.copy(2, whole=True))
    assert (a["count"], a["rows_failed"]) == (2, 0)
    assert accepted(mail) == [1, 2]


def test_two_rows_of_one_conversation_count_once():
    mail = lunch()
    a = file_many(mail, "archive", mail.copy(1, whole=True), mail.copy(2, whole=True))
    assert (a["count"], a["failed"], a["rows_failed"]) == (2, 0, 0)
    assert accepted(mail) == [1, 2]


def test_junk_never_stands_for_a_conversation():
    mail = lunch()
    a = file_many(mail, "junk", mail.copy(2, whole=True))
    assert a["count"] == 1 and accepted(mail) == [2]


def test_a_row_that_is_gone_fails_alone():
    mail = lunch()
    a = file_many(mail, "archive", {"key": 999_999, "folder": "INBOX"}, mail.copy(5))
    assert (a["ok"], a["count"], a["failed"], a["rows_failed"]) == (True, 1, 0, 1)    # a copy gone is no message tried
    assert a["filed"] == [{"key": 105, "folder": "INBOX", "uid": 5}]


def test_one_the_server_refuses_goes_back_alone():
    mail = lunch()
    mail.outcome[2] = command.REFUSED
    a = file_many(mail, "archive", mail.copy(5), mail.copy(2))
    assert (a["ok"], a["count"], a["failed"], a["rows_failed"]) == (True, 1, 1, 1)
    assert a["filed"] == [{"key": 105, "folder": "INBOX", "uid": 5}]


def test_a_conversation_the_server_refuses_in_part_is_partial():
    mail = lunch()
    mail.outcome[1] = command.REFUSED
    a = file_many(mail, "archive", mail.copy(2, whole=True))
    assert (a["count"], a["failed"], a["rows_failed"], a["partial"]) == (1, 1, 0, True)


def test_a_click_waits_about_as_long_as_for_one_and_the_worker_has_the_rest():
    mail = lunch()
    mail.FILE_WAIT = 0
    a = file_many(mail, "archive", mail.copy(5), mail.copy(1), mail.copy(2))
    assert (a["ok"], a["count"], a["undoable"]) == (True, 3, True)
    assert mail.log == [("accept", 5), ("accept", 1), ("accept", 2), ("try", 5), ("left", 1), ("left", 2)]   # they left the list together
