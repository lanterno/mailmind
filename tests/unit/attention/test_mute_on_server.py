"""Muting a thread marks its unread members read on the server too (decision D6), through the Mailbox's queue
(mailmind.attention.application.muting.Muting, on in-memory fakes behind its ports): what is unread is queued as read and the queue
woken, and a thread read already, or muted already, queues nothing and wakes nothing. The queue reaching the server, Undo and the
tray are tests/integration/attention/test_mute_on_server.py's."""
from contextlib import contextmanager

from mailmind.attention.application.muting import Muting
from mailmind.attention.application.threads import Threads

ADA = "ada@example.test"
THREAD = "rf:<lunch@example.test>"
LUNCH = {"pk": 7, "account": ADA, "folder": "INBOX", "message_key": 41, "thread_id": THREAD}
REPLY = {"pk": 8, "account": ADA, "folder": "INBOX", "message_key": 42, "thread_id": THREAD}
THE_THREAD = {"account": ADA, "thread_id": THREAD}


class Uow:
    @contextmanager
    def read(self):
        yield None

    write = read


class Book:
    """The thread's mail as the book answers for it: what is unread in it, and whether it is muted."""
    def __init__(self, unread=(REPLY,)):
        self.unread, self.muted = list(unread), set()

    def is_muted(self, c, account, thread_id):
        return (account, thread_id) in self.muted

    def unread_in_thread(self, c, account, thread_id):
        return list(self.unread)

    def a_member(self, c, account, thread_id):
        return LUNCH

    def mute_thread(self, c, account, thread_id, when):
        self.muted.add((account, thread_id))


class Mail:
    """The Mailbox's queue: each flag change asked of it, and how often it was woken."""
    def __init__(self):
        self.queued, self.woken = [], 0

    def get(self, c, pk):
        return None

    def queue_flags(self, c, row, flags, mutation_id, when):
        self.queued.append((row["pk"], flags))
        return True

    def flags_queued(self):
        self.woken += 1


class Accounts:
    def emails(self):
        return [ADA]


class Undo:
    def push(self, entry, group):
        pass


def muting(book, mail):
    return Muting(Uow(), book, mail, Threads(mail, Accounts()), Undo())


def test_muting_queues_the_unread_members_as_read_and_wakes_the_queue():
    book, mail = Book(), Mail()
    muting(book, mail).mute(THE_THREAD)
    assert (mail.queued, mail.woken) == ([(REPLY["pk"], {"seen": True})], 1)


def test_muting_a_thread_already_read_asks_nothing_of_the_server():
    book, mail = Book(unread=()), Mail()
    assert muting(book, mail).mute(THE_THREAD)["ok"]
    assert (mail.queued, mail.woken) == ([], 0)
    assert (ADA, THREAD) in book.muted


def test_muting_again_queues_nothing_more():
    book, mail = Book(), Mail()
    use = muting(book, mail)
    use.mute(THE_THREAD)
    before = (list(mail.queued), mail.woken)
    assert use.mute(THE_THREAD)["ok"]
    assert (mail.queued, mail.woken) == before
