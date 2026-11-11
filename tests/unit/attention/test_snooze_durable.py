"""A message that wakes is unread on the server too, or the next sync that read its flags would take that back
(mailmind.attention.application.snoozing.Snoozing.wake_due, on in-memory fakes behind its ports): read mail is queued as unread
and the queue woken, and unread mail asks nothing. How a snooze outlives its row, and the mark reaching the server, are
tests/integration/attention/test_snooze_durable.py's."""
from contextlib import contextmanager

from mailmind.attention.application.snoozing import Snoozing

ADA = "ada@example.test"
READ = {"pk": 7, "account": ADA, "folder": "INBOX", "message_key": 41, "seen": 1}
UNREAD = {"pk": 8, "account": ADA, "folder": "INBOX", "message_key": 42, "seen": 0}


class Uow:
    @contextmanager
    def read(self):
        yield None

    write = read


class Book:
    """The snoozes that have come due, and those still kept."""
    def __init__(self, due):
        self.due, self.snoozed = list(due), {r["pk"] for r in due}

    def due_snoozes(self, c, now_ts):
        return [r for r in self.due if r["pk"] in self.snoozed]

    def clear_snooze(self, c, pk):
        self.snoozed.discard(pk)


class Mail:
    """The Mailbox's queue: each flag change asked of it, and how often it was woken."""
    def __init__(self):
        self.queued, self.woken = [], 0

    def queue_flags(self, c, row, flags, mutation_id, when):
        self.queued.append((row["pk"], flags))
        return True

    def flags_queued(self):
        self.woken += 1


class Undo:
    def push(self, entry, group):
        pass


def wake(*due):
    book, mail = Book(due), Mail()
    Snoozing(Uow(), book, mail, Undo()).wake_due()
    return book, mail


def test_waking_marks_read_mail_unread_through_the_queue():
    book, mail = wake(READ)
    assert book.snoozed == set()
    assert (mail.queued, mail.woken) == ([(READ["pk"], {"seen": False})], 1)


def test_waking_unread_mail_asks_nothing_of_the_server():
    book, mail = wake(UNREAD)
    assert book.snoozed == set()
    assert (mail.queued, mail.woken) == ([], 0)


def test_waking_both_marks_only_the_read_one():
    _, mail = wake(READ, UNREAD)
    assert (mail.queued, mail.woken) == ([(READ["pk"], {"seen": False})], 1)
