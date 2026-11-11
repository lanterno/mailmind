"""Two changes to one kept document at once, the first held after its read.

A change that reads and writes in one write block keeps the second waiting for the write lock until the first commits, so
the second reads what the first wrote. One that reads outside its write lets the second read what the first is about to
change, and whichever writes last loses the other's.
"""
import threading

from waiting import blocked_in, wait_until

from mailmind.platform.db.uow import UnitOfWork


class HeldAfterItsRead:
    """A store (a module with read(c) and write(c, value)), but its first read holds there until `overlap` lets it go."""

    def __init__(self, store):
        self._store = store
        self.reads, self.first_read, self.go = [], threading.Event(), threading.Event()

    def read(self, c):
        held = self._store.read(c)
        self.reads.append(threading.current_thread())
        if len(self.reads) == 1:
            self.first_read.set()
            self.go.wait(30)
        return held

    def write(self, c, value):
        self._store.write(c, value)


def overlap(held, first, second):
    """Runs `first` until it has read through `held`, then `second` until it has read too or waits for the write lock, then
    lets the first go on. Returns once both have ended, and raises what either raised."""
    raised = []

    def run(change):
        try:
            change()
        except BaseException as e:
            raised.append(e)

    one = threading.Thread(target=run, args=[first])
    one.start()
    assert held.first_read.wait(30)
    two = threading.Thread(target=run, args=[second])
    two.start()
    assert wait_until(lambda: len(held.reads) == 2 or blocked_in(two, UnitOfWork.write.__wrapped__))
    held.go.set()
    one.join()
    two.join()
    if raised:
        raise raised[0]
