"""Filing a whole conversation (mailmind.mailbox.application.filing.Filing.do_thread_file), on a stand-in for the store and the
command path: which messages a conversation's row stands for, every one accepted before any is tried, and a try that raises
costing only its own message; and what the open tabs are told of moves that landed within the request. Which rows of the
store a thread holds, Undo, a crash and the fake server are the integration half's."""
import contextlib

import pytest

from mailmind.kernel.errors import Conflict
from mailmind.mailbox.application.filing import Filing
from mailmind.mailbox.domain import command
from mailmind.platform import sse

ADA = "ada@example.test"


class _Mail:
    """What Filing reads of Ada's Inbox (pk -> its key, uid and thread) and asks of the command path. `raises` says what a try
    at a message raises; `log` holds each step in order."""
    FILE_WAIT = 2.5

    def __init__(self, *rows):
        self.rows = {r["pk"]: {"account": ADA, "folder": "INBOX", "uid": r["pk"], "message_key": 100 + r["pk"], **r} for r in rows}
        self.raises, self.log = {}, []
        self.outcomes = {}              # what a try at a message comes to, by its pk: applied where none is said

    def get_message(self, c, pk):
        return self.rows.get(pk)

    def thread_pks_in(self, c, account, folder, thread_id):
        return [p for p, r in self.rows.items() if (r["account"], r["folder"], r["thread_id"]) == (account, folder, thread_id)]

    def names_of(self, c, pks):
        return {p: (self.rows[p]["message_key"], self.rows[p]["folder"], self.rows[p]["uid"]) for p in pks if p in self.rows}

    def accept_move_within(self, c, kind, pk, mutation_id, push_undo=None, to=None):
        self.log.append(("accept", pk))
        push_undo(c, {"pk": pk})
        return command.Accepted(pk, ADA, True, True, {"id": pk})

    def accept_move(self, kind, pk, mutation_id, push_undo=None, to=None):
        return self.accept_move_within("c", kind, pk, mutation_id, push_undo or (lambda c, entry: None), to)

    def pk_in(self, c, key, folder):
        return next((p for p, r in self.rows.items() if (r["message_key"], r["folder"]) == (key, folder)), None)

    def submit(self, accepted):
        self.log.append(("try", accepted.id))
        if accepted.id in self.raises:
            raise self.raises[accepted.id]
        return self.outcomes.get(accepted.id, command.APPLIED)

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


def filing(mail):
    return Filing(_Uow(), mail, None, mail, None, _Aside(), _Aside(), _Aside(), None)


def archive(mail, pk):
    return filing(mail).do_thread_file("/api/thread/archive", {"pk": pk})


def test_a_message_without_a_thread_is_a_conversation_of_one():
    mail = _Mail({"pk": 5, "thread_id": None}, {"pk": 6, "thread_id": ""}, {"pk": 7, "thread_id": None}, {"pk": 8, "thread_id": ""})
    r = archive(mail, 5)
    assert (r["filed"], r["count"]) == ([{"key": 105, "folder": "INBOX", "uid": 5}], 1)
    r = archive(mail, 6)
    assert (r["filed"], r["count"]) == ([{"key": 106, "folder": "INBOX", "uid": 6}], 1)
    assert mail.log == [("accept", 5), ("try", 5), ("accept", 6), ("try", 6)]


def test_the_whole_conversation_leaves_the_lists_before_the_first_try():
    mail = _Mail({"pk": 1, "thread_id": "rf:lunch"}, {"pk": 2, "thread_id": "rf:lunch"})
    assert archive(mail, 2)["count"] == 2
    assert mail.log == [("accept", 1), ("accept", 2), ("try", 1), ("try", 2)]


def test_a_try_that_raises_leaves_it_queued_and_the_rest_still_go():
    mail = _Mail({"pk": 1, "thread_id": "rf:lunch"}, {"pk": 2, "thread_id": "rf:lunch"})
    mail.raises[1] = RuntimeError("something nobody expected")
    r = archive(mail, 2)
    assert (r["ok"], r["count"], r["failed"], r["queued"], r["undoable"]) == (True, 2, 0, 1, True)
    assert [pk for step, pk in mail.log if step == "try"] == [1, 2]


# -- what the open tabs are told ------------------------------------------------------------------------------------------

@pytest.fixture
def told(monkeypatch):
    heard = []
    monkeypatch.setattr(sse, "publish", lambda topic, data=None: heard.append(topic))
    return heard


def lunch(n):
    return _Mail(*({"pk": p, "thread_id": "rf:lunch"} for p in range(1, n + 1)))


def test_a_conversation_filed_within_the_request_tells_the_open_tabs_once(told):
    assert archive(lunch(20), 20)["count"] == 20
    assert told == ["commands-changed"]


def test_a_conversation_the_worker_has_tells_of_the_tray_and_of_no_landing(told):
    mail = lunch(2)
    mail.outcomes = {1: command.QUEUED, 2: command.WAITING}
    assert archive(mail, 2)["queued"] == 2
    assert told == ["outbox", "outbox"]


def test_moves_that_landed_beside_one_left_to_the_worker_tell_of_each_once(told):
    mail = lunch(3)
    mail.raises[2] = RuntimeError("something nobody expected")
    assert archive(mail, 3)["queued"] == 1
    assert sorted(told) == ["commands-changed", "outbox"]


def test_a_conversation_none_of_which_went_tells_nothing(told):
    mail = lunch(2)
    mail.outcomes = {1: command.REFUSED, 2: command.REFUSED}
    with pytest.raises(Conflict):
        archive(mail, 2)
    assert told == []


def test_one_message_filed_within_the_request_tells_the_open_tabs(told):
    assert filing(_Mail({"pk": 5, "thread_id": None})).do_write("/api/archive", {"pk": 5})["msg"] == "archived"
    assert told == ["commands-changed"]


def test_a_plan_tells_the_open_tabs_once_for_every_move_it_carried_out(told):
    done = filing(lunch(5)).carry_out("archive", None, [(100 + p, "INBOX") for p in range(1, 6)])
    assert (done["count"], done["queued"]) == (5, 0)
    assert told == ["commands-changed"]


def test_a_plan_whose_moves_all_went_to_the_worker_tells_of_no_landing(told):
    mail = lunch(2)
    mail.outcomes = {1: command.QUEUED, 2: command.QUEUED}
    assert filing(mail).carry_out("trash", None, [(101, "INBOX"), (102, "INBOX")])["queued"] == 2
    assert told == ["outbox", "outbox"]
