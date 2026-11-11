"""The command queue's worker (MailCommands.worker_loop), as the app starts it on a thread of its own: it puts back what a
crash left running and applies it, and it drains through MailCommands.drain, so a read-only process applies nothing. Each
test stops it when done, which the app's never is."""
import threading

import pytest

from fixtures import world as w
from world_helpers import wait_until

NAME = "bea-inbox-01-client.eml"


def left_running(command_id):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        c.execute("UPDATE mail_commands SET state='running' WHERE id=?", (command_id,))
        c.commit()
    finally:
        c.close()


def state(command_id):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return c.execute("SELECT state, applied FROM mail_commands WHERE id=?", (command_id,)).fetchone()[:]
    finally:
        c.close()


@pytest.fixture
def worker(monkeypatch):
    """Start the worker as the app does, with a way to stop it. `start()` returns an Event set after each drain it makes."""
    from mailmind.bootstrap.mailbox import commands
    stop, drained = threading.Event(), threading.Event()
    run_forever = commands.QUEUE.run_forever

    def drain_and_say(drain):
        drain()
        drained.set()
    monkeypatch.setattr(commands.QUEUE, "run_forever", lambda drain: run_forever(lambda: drain_and_say(drain), stop))
    lane = threading.Thread(target=commands.worker_loop, name="commands-worker", daemon=True)

    def start():
        lane.start()
        commands.kick()
        return drained
    yield start
    stop.set()
    commands.kick()
    if lane.is_alive():
        lane.join(5)
    assert not lane.is_alive()


def test_what_a_crash_left_running_is_put_back_and_applied(world, worker):
    command_id = world.queued_move(NAME, "Folders/Clients")
    left_running(command_id)
    worker()
    assert wait_until(lambda: state(command_id) == ("done", 1))
    assert [s[:2] for s in world.where(NAME)["server"]] == [[w.BEA, "Folders/Clients"]]


def test_a_read_only_process_applies_nothing(world, worker, monkeypatch):
    from mailmind.bootstrap.mailbox import commands
    monkeypatch.setenv("MAILMIND_PROFILE", "read-only")
    claimed = []
    monkeypatch.setattr(commands.QUEUE, "drain", lambda *a: claimed.append(a))
    command_id = world.queued_move(NAME, "Folders/Clients")
    assert worker().wait(5)
    assert claimed == [] and state(command_id) == ("queued", 0)
    assert [s[:2] for s in world.where(NAME)["server"]] == [[w.BEA, "INBOX"]]
