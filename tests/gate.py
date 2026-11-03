"""Holding the IMAP gate from a test, as a long mail operation would."""
import contextlib
import threading

from mailmind.platform.workers import gates


@contextlib.contextmanager
def held_elsewhere(account=gates.EVERY_ACCOUNT):
    """Hold `account`'s gate from another thread until the block ends. It goes through gates.run
    like any job, so it holds whatever the gate is: today's one lock, or an account's worker."""
    holding, release = threading.Event(), threading.Event()

    def job():
        holding.set()
        release.wait(60)
    t = threading.Thread(target=lambda: gates.run(account, job, priority=gates.Priority.INTERACTIVE),
                         name="gate-holder", daemon=True)
    t.start()
    assert holding.wait(10), "the gate never came free"
    try:
        yield
    finally:
        release.set()
        t.join(10)
