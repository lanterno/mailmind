"""The tasks a person confirmed (mailmind.attention.application.tasks.Tasks), on in-memory fakes behind its ports: what a person
confirmed is read as their input, so a title comes to one line, a date is optional, and one that won't do is refused in words with
nothing kept; and a task whose account is turned off is kept but not listed. How they are stored, linked and listed over the
database, and the routes, are tests/integration/attention/test_tasks.py's."""
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
import time_machine

from mailmind.attention.application.tasks import Tasks
from mailmind.kernel.errors import NotFound, Refused

ADA = "ada@example.test"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
LUNCH = {"pk": 7, "account": ADA, "folder": "INBOX", "message_key": 41}


class Uow:
    @contextmanager
    def read(self):
        yield None

    write = read


class Book:
    """The tasks table, as Tasks asks it."""
    def __init__(self):
        self.tasks = {}

    def add_task(self, c, account, key, folder, title, due, when):
        tid = len(self.tasks) + 1
        self.tasks[tid] = {"id": tid, "account": account, "message_key": key, "folder": folder, "title": title, "due": due,
                           "done_at": None, "uid": 3, "thread_id": "rf:<lunch@example.test>", "subject": "Lunch on Thursday?",
                           "from_name": "Lin", "from_addr": "lin@example.test"}
        return tid

    def task_rows(self, c, task_id=None):
        return [dict(t) for t in self.tasks.values() if task_id in (None, t["id"])]


class Mail:
    def __init__(self, rows=(LUNCH,)):
        self.rows = {r["pk"]: r for r in rows}

    def get(self, c, pk):
        return self.rows.get(pk)


class Accounts:
    def __init__(self, emails=(ADA,)):
        self.on = list(emails)

    def emails(self):
        return self.on


def tasks(book, mail=None, accounts=None):
    return Tasks(Uow(), book, mail or Mail(), accounts or Accounts())


def add(book, **kw):
    with time_machine.travel(NOW, tick=False):
        return tasks(book).add({"pk": 7, "title": "Send the slides", "due": "2026-11-09", **kw})


def test_a_task_is_kept_with_its_date():
    book = Book()
    task = add(book)["task"]
    assert (task["title"], task["due"], task["done"], task["key"], task["folder"]) == ("Send the slides", "2026-11-09", False,
                                                                                      41, "INBOX")


def test_a_task_needs_no_date():
    book = Book()
    assert add(book, due=None)["task"]["due"] is None
    assert add(book, title="Check it", due="")["task"]["due"] is None


@pytest.mark.parametrize("fields, said", [
    ({"title": "   "}, "a task needs a title"),
    ({"due": "next Friday"}, "that date isn’t one this can use"),
    ({"due": "2199-01-01"}, "that date isn’t one this can use"),
], ids=["no-title", "not-a-date", "too-far-off"])
def test_what_wont_do_is_refused_in_words_and_nothing_is_kept(fields, said):
    book = Book()
    with pytest.raises(Refused) as e:
        add(book, **fields)
    assert str(e.value) == said and book.tasks == {}


def test_a_title_comes_to_one_line():
    assert add(Book(), title="Sign\r\nthe\tform")["task"]["title"] == "Sign the form"


@pytest.mark.parametrize("row", [None, {**LUNCH, "message_key": None}], ids=["no-copy", "no-key"])
def test_a_message_not_there_to_name_makes_nothing(row):
    book = Book()
    with pytest.raises(NotFound):
        Tasks(Uow(), book, Mail([row] if row else []), Accounts()).add({"pk": 7, "title": "Send the slides"})
    assert book.tasks == {}


def test_an_account_turned_off_has_its_tasks_kept_and_not_listed():
    book = Book()
    add(book)
    assert [t["title"] for t in tasks(book).listing()["items"]] == ["Send the slides"]
    assert tasks(book, accounts=Accounts(emails=())).listing()["items"] == []
    assert len(book.tasks) == 1
