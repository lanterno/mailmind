"""Chaos against the fake servers: what one account's trouble may and may not do to the rest, and
what survives a connection lost at the worst moment. With a gate for each account
(MAILMIND_WORKERS=account), a black-holed account holds up only its own work."""
import threading
import time

import pytest

from fixtures import world as w
from mailmind.platform.workers import gates


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
    import store
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


def boxes(world, name):
    """Where `name` is: [(account, folder)] on the server, and in the database."""
    got = world.where(name)
    return sorted((s[0], s[1]) for s in got["server"]), sorted((d["account"], d["folder"]) for d in got["db"])


@pytest.mark.parametrize("command", ["UID MOVE", "UID COPY"])
def test_a_connection_dropped_mid_move_recovers(app, world, per_account, command):
    """The server moved it (or, without MOVE, copied it), then the connection dropped before it
    said so. The move is queued, and its drain leaves one copy, in the Archive."""
    import accounts
    import ingest
    import readmutq
    name = "ada-inbox-05-receipt.eml"
    if command == "UID COPY":
        world.server.without = {"MOVE"}
    world.server.drop_after(command, user=w.ADA)
    r = app.post("/api/archive", {"pk": world.pk(name), "mutation_id": "m-1"}).json
    assert r["status"] == "queued"
    readmutq.drain()
    ingest.sync_folder(accounts.get(w.ADA), "Archive")
    assert boxes(world, name) == ([(w.ADA, "Archive")], [(w.ADA, "Archive")])


def late(n=1):
    return w.variant("ada-inbox-02-lunch-confirm.eml", Message_ID=f"<late-{n}@example.test>",
                     Subject=f"Re: Lunch, late word {n}")


def stored(message_id):
    import store
    c = store.conn()
    try:
        return c.execute("SELECT COUNT(*) FROM messages WHERE message_id=?", (message_id,)).fetchone()[0]
    finally:
        c.close()


def test_an_idle_event_during_a_sync_isnt_lost(app, world, monkeypatch):
    """IDLE hears Ada's new mail while a sync of Bea runs. The running sync goes round again for
    Ada as it ends, instead of leaving her mail for the next scheduled sync, minutes away."""
    import realtime
    import server
    from mailmind.platform import bus
    running, release = threading.Event(), threading.Event()
    sync = server.sync_accounts

    def slow_first(emails=None):
        if not running.is_set():
            running.set()
            release.wait(10)
        return sync(emails)
    monkeypatch.setattr(server, "sync_accounts", slow_first)
    first = threading.Thread(target=lambda: server.sync_guarded({w.BEA}), daemon=True)
    first.start()
    try:
        assert running.wait(5)
        world.server.deliver(w.ADA, "INBOX", late(1))
        bus.publish(realtime.InboxChanged(frozenset({w.ADA})))   # the app wired IDLE's news to a sync
        world.server.deliver(w.ADA, "INBOX", late(2))
        bus.publish(realtime.InboxChanged(frozenset({w.ADA})))   # and again: one more round covers both
    finally:
        release.set()
        first.join(10)
    assert not first.is_alive()
    assert (stored("<late-1@example.test>"), stored("<late-2@example.test>")) == (1, 1)
    assert not server._sync["running"]
