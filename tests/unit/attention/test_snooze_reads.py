"""What Undo gives back of a snooze (mailmind.attention.application.snoozing.Snoozing, on in-memory fakes behind its ports): a
snooze set over another goes back to the time the first one had, found by the message's key and folder. Whether a message is
snoozed, in every list, is tests/integration/attention/test_snooze_reads.py's."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import time_machine

from mailmind.attention.application.snoozing import Snoozing

ADA = "ada@example.test"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
FIRST = int((NOW + timedelta(hours=3)).timestamp())
SECOND = FIRST + 3600
LUNCH = {"pk": 7, "account": ADA, "folder": "INBOX", "message_key": 41}


class Uow:
    @contextmanager
    def read(self):
        yield None

    write = read


class Book:
    """When each snoozed message wakes, by pk."""
    def __init__(self):
        self.snoozes = {}

    def set_snooze(self, c, pk, until):
        self.snoozes[pk] = until

    def clear_snooze(self, c, pk):
        self.snoozes.pop(pk, None)


class Mail:
    """The message's row as it stands, its snooze read off the book, and its copy found by key and folder."""
    def __init__(self, book):
        self.book = book

    def get(self, c, pk):
        return {**LUNCH, "snooze_until": self.book.snoozes.get(pk)} if pk == LUNCH["pk"] else None

    def copy_in_folder(self, c, key, folder):
        return LUNCH["pk"] if (key, folder) == (LUNCH["message_key"], LUNCH["folder"]) else None

    def index(self, c, pk):
        pass


class Undo:
    def __init__(self):
        self.stack = []

    def push(self, entry, group):
        self.stack.append(entry)


def test_undo_of_a_second_snooze_gives_back_the_first_time():
    book, undo = Book(), Undo()
    use = Snoozing(Uow(), book, Mail(book), undo)
    with time_machine.travel(NOW, tick=False):
        use.snooze({"pks": [LUNCH["pk"]], "until": FIRST})
        use.snooze({"pks": [LUNCH["pk"]], "until": SECOND})
    use.reverse(undo.stack.pop())
    assert book.snoozes == {LUNCH["pk"]: FIRST}
    use.reverse(undo.stack.pop())
    assert book.snoozes == {}
