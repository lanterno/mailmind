"""Chaos against the fake servers: what one account's trouble may and may not do to the rest, and
what survives a connection lost at the worst moment. With a gate for each account
(MAILMIND_WORKERS=account), a black-holed account holds up only its own work."""
import threading
import time
from datetime import timedelta

import pytest

from fixtures import world as w
from mailmind.platform.workers import gates
from mailmind.mailbox.domain.events import InboxChanged
from mailmind.bootstrap import mailbox


def wait_until(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


@pytest.fixture
def per_account(monkeypatch):
    monkeypatch.setenv("MAILMIND_WORKERS", "account")


def unfetched(world, name):
    """`name`'s pk, its body not yet fetched: opening it goes to the server."""
    from mailmind.bootstrap.mailbox import store
    pk = world.pk(name)
    c = store.conn()
    try:
        c.execute("UPDATE messages SET body_fetched=0 WHERE pk=?", (pk,))
        c.commit()
    finally:
        c.close()
    return pk


def test_a_black_holed_account_doesnt_slow_another_accounts_body_open(app, world, per_account):
    world.server.stall("EXAMINE", user=w.BEA)                 # Bea's server takes the login, then nothing
    bea = unfetched(world, "bea-inbox-01-client.eml")
    hung = threading.Thread(target=lambda: app.get(f"/api/message/{bea}/body"), daemon=True)
    hung.start()
    try:
        assert wait_until(lambda: world.server.commands_for(w.BEA, "EXAMINE")) and gates.held(w.BEA)
        ada = [m[0] for m in w.MAIL if m[0].startswith("ada-inbox-")]
        took = []
        for name in ada * 2:
            pk = unfetched(world, name)
            began = time.monotonic()
            assert app.get(f"/api/message/{pk}/body").json["ok"]
            took.append(time.monotonic() - began)
        p95 = sorted(took)[max(0, round(0.95 * len(took)) - 1)]
        assert p95 < 1.0, f"p95 {p95:.2f}s over {len(took)} opens"
        assert max(took) < 5, f"one open took {max(took):.0f}s"     # not the black hole's 60 s timeout
        assert hung.is_alive()                                 # Bea's open still waits on her server
    finally:
        world.server.release_stalls()
        hung.join(30)
    assert not hung.is_alive()


@pytest.mark.parametrize("path", ["/api/reconcile", "/api/sweep"])
def test_a_run_over_every_account_waits_only_for_the_account_that_is_busy(app, world, per_account, path):
    import gate
    logins = {who: len(world.server.commands_for(who, "LOGIN")) for who in (w.ADA, w.CY)}
    answers = []
    with gate.held_elsewhere(w.BEA):                           # a long job on Bea's server
        run = threading.Thread(target=lambda: answers.append(app.post(path, {"account": "all", "dry_run": True})),
                               daemon=True)
        run.start()
        assert wait_until(lambda: len(world.server.commands_for(w.ADA, "LOGIN")) > logins[w.ADA])
        assert wait_until(lambda: gates.held(w.BEA) and not gates.held(w.ADA))
        assert run.is_alive() and len(world.server.commands_for(w.CY, "LOGIN")) == logins[w.CY]
    run.join(30)
    assert not run.is_alive() and answers[0].json["ok"]
    assert [r["account"] for r in answers[0].json["results"]] == [w.ADA, w.BEA, w.CY]


def test_a_black_holed_accounts_sent_copy_doesnt_hold_up_another_accounts_send(app, world, per_account):
    from mailmind.bootstrap.compose import outbox
    world.server.stall("APPEND", user=w.BEA)                   # Bea's server takes the send's copy, then nothing
    for who, key in ((w.BEA, "k-bea"), (w.ADA, "k-ada")):     # Bea's first: the outbox goes in order
        assert app.post("/api/send", {"account": who, "mode": "new", "to": ["Lin Mori <lin@example.test>"],
                                      "subject": "Fabric order", "text": "The linen came in.",
                                      "idempotency_key": key}).json["ok"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    drain = threading.Thread(target=outbox.drain, daemon=True)
    drain.start()
    try:
        assert wait_until(lambda: world.server.commands_for(w.BEA, "APPEND"))
        assert wait_until(lambda: len(world.smtp.messages) == 2), "Ada's send waits on Bea's server"
    finally:
        world.server.release_stalls()
        drain.join(60)
    assert not drain.is_alive()


def test_the_outbox_worker_sends_on_while_another_accounts_sent_copy_hangs(app, world, per_account):
    """The worker's drains don't wait for a busy account: a send that comes due while Bea's Sent
    copy hangs goes out at once, not after her server's 60-second timeout."""
    from mailmind.bootstrap.compose import outbox
    from mailmind.bootstrap.mailbox import store

    def send(who, key):
        return app.post("/api/send", {"account": who, "mode": "new", "to": ["Lin Mori <lin@example.test>"],
                                      "subject": "Fabric order", "text": "The linen came in.",
                                      "idempotency_key": key}).json["outbox_id"]

    def state(oid):
        c = store.conn()
        try:
            return tuple(c.execute("SELECT state, delivered FROM outbox WHERE id=?", (oid,)).fetchone())
        finally:
            c.close()
    world.server.stall("APPEND", user=w.BEA)
    bea, _ = send(w.BEA, "k-bea"), send(w.ADA, "k-ada-1")
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    stop = threading.Event()
    worker = threading.Thread(target=lambda: outbox.QUEUE.run_forever(outbox.drain, stop), daemon=True)
    worker.start()                                              # what outbox.worker_loop runs, with a stop
    try:
        assert wait_until(lambda: world.server.commands_for(w.BEA, "APPEND"))
        assert wait_until(lambda: len(world.smtp.messages) == 2)
        send(w.ADA, "k-ada-2")
        app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
        outbox.kick()
        assert wait_until(lambda: len(world.smtp.messages) == 3), "Ada's next send waits on Bea's server"
        assert state(bea) == ("sending", 1)                    # Bea's went out; her copy still hangs
    finally:
        world.server.release_stalls()
        stop.set()
        outbox.kick()
        worker.join(60)
    assert not worker.is_alive()
    assert wait_until(lambda: state(bea) == ("queued", 1))     # its copy tried again later


def boxes(world, name):
    """Where `name` is: [(account, folder)] on the server, and in the database."""
    got = world.where(name)
    return sorted((s[0], s[1]) for s in got["server"]), sorted((d["account"], d["folder"]) for d in got["db"])


@pytest.mark.parametrize("command", ["UID MOVE", "UID COPY"])
def test_a_connection_dropped_mid_move_recovers(app, world, per_account, command):
    """The server moved it (or, without MOVE, copied it), then the connection dropped before it
    said so. The move is queued, and its drain leaves one copy, in the Archive."""
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    from mailmind.bootstrap.mailbox import commands
    name = "ada-inbox-05-receipt.eml"
    if command == "UID COPY":
        world.server.without = {"MOVE"}
    world.server.drop_after(command, user=w.ADA)
    r = app.post("/api/archive", {"pk": world.pk(name), "mutation_id": "m-1"}).json
    assert r["status"] == "queued"
    commands.drain()
    ingest.sync_folder(accounts.get(w.ADA), "Archive")
    assert boxes(world, name) == ([(w.ADA, "Archive")], [(w.ADA, "Archive")])


def late(n=1):
    return w.variant("ada-inbox-02-lunch-confirm.eml", Message_ID=f"<late-{n}@example.test>",
                     Subject=f"Re: Lunch, late word {n}")


def stored(message_id):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return c.execute("SELECT COUNT(*) FROM messages WHERE message_id=?", (message_id,)).fetchone()[0]
    finally:
        c.close()


def test_an_idle_event_during_a_sync_isnt_lost(app, world, monkeypatch):
    """IDLE hears Ada's new mail while a sync of Bea runs. The running sync goes round again for
    Ada as it ends, instead of leaving her mail for the next scheduled sync, minutes away."""
    from mailmind.platform import bus
    running, release = threading.Event(), threading.Event()
    sync = mailbox.syncing.sync_accounts

    def slow_first(emails=None):
        if not running.is_set():
            running.set()
            release.wait(10)
        return sync(emails)
    monkeypatch.setattr(mailbox.syncing, "sync_accounts", slow_first)
    first = threading.Thread(target=lambda: mailbox.syncing.sync_guarded({w.BEA}), daemon=True)
    first.start()
    try:
        assert running.wait(5)
        world.server.deliver(w.ADA, "INBOX", late(1))
        bus.publish(InboxChanged(frozenset({w.ADA})))   # the app wired IDLE's news to a sync
        world.server.deliver(w.ADA, "INBOX", late(2))
        bus.publish(InboxChanged(frozenset({w.ADA})))   # and again: one more round covers both
    finally:
        release.set()
        first.join(10)
    assert not first.is_alive()
    assert (stored("<late-1@example.test>"), stored("<late-2@example.test>")) == (1, 1)
    assert not mailbox.syncing._sync["running"]
