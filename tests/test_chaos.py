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
