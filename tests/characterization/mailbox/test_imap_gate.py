"""The IMAP gate: held for every mail move, a worker's included, and never waited on where it would freeze the app."""
import threading
from datetime import timedelta

import gate
from fixtures import world as w
from mailmind.bootstrap import accounts
from mailmind.bootstrap import curation
from mailmind.bootstrap import mailbox
from mailmind.platform.workers import gates
from world_helpers import FOLDED, RECEIPT, acct, body_of


def lock_spy(world, command, user=None):
    """Whether the app held the account's IMAP gate each time the fake IMAP server received
    `command`. The predicate never matches, so the command runs as usual."""
    held = []

    def look(args):
        held.append(gates.held(user or gates.EVERY_ACCOUNT))
        return False
    world.server.fail(command, when=look, times=None, user=user)
    return held


def test_hwf_trash_moves_under_the_imap_lock_and_queues_when_it_cant(app, world):
    held = lock_spy(world, "UID MOVE", user=w.ADA)
    assert app.post("/api/trash", {**world.copy(RECEIPT)}).json["ok"]
    assert held == [True]
    world.server.drop("SELECT", user=w.ADA)
    assert app.post("/api/trash", {**world.copy(FOLDED)}).json["status"] == "queued"


def test_hwf_testing_an_account_never_waits_on_the_imap_lock(app, world):
    held = lock_spy(world, "LOGIN", user=w.ADA)
    r = app.post("/api/accounts/test", {"email": w.ADA}).json
    assert (r["ok"], r["connect"], held) == (True, True, [False])


def test_hwf_background_reconcile_and_expiry_yield_to_a_user_op(app, world):
    from mailmind.bootstrap.curation import rules
    rules.set_expiry("nobody.example.test", 72)              # so expiry has work that needs IMAP
    ada = accounts.get(w.ADA)
    since = len(world.server.commands)
    with gate.held_elsewhere():                              # a user op holds the lock
        mailbox.reconciler._auto_reconcile_one(ada)
        curation.expiry.auto_one(ada)
    assert world.server.commands[since:] == []               # both skipped this cycle
    mailbox.reconciler._auto_reconcile_one(ada)
    curation.expiry.auto_one(ada)
    assert len(world.server.commands_for(w.ADA, "LOGIN")) >= 2   # and run once the lock is free


def test_hwf_reconcile_takes_the_gate_once_for_each_folder(app, world, monkeypatch):
    from mailmind.bootstrap.mailbox import reconciler
    real, jobs = gates.run, []

    def counted(account, fn, **kw):
        jobs.append(account)
        return real(account, fn, **kw)
    monkeypatch.setattr(gates, "run", counted)
    mailbox.reconciler._auto_reconcile_one(accounts.get(w.ADA))              # never holds the gate across folders
    assert len(reconciler.reconciled_folders(w.ADA)) > 1
    assert jobs == [w.ADA] * len(reconciler.reconciled_folders(w.ADA))


def test_hwf_a_prefetch_never_waits_for_the_imap_lock(app, world):
    prefetch = body_of(world.copy("bea-inbox-01-client.eml"), prefetch=1)
    fetches = len(world.server.commands_for(w.BEA, "UID FETCH"))
    with gate.held_elsewhere():
        r = app.get(prefetch).json
    assert r == {"ok": True, "prefetched": False}                  # it gave way, and the open will fetch it
    assert len(world.server.commands_for(w.BEA, "UID FETCH")) == fetches
    assert app.get(prefetch).json == {"ok": True, "prefetched": True}
    with gate.held_elsewhere():                              # already warm: no lock needed
        assert app.get(prefetch).json == {"ok": True, "prefetched": True}


def test_hwf_smtp_delivery_holds_no_imap_lock(app, world):
    from mailmind.bootstrap.compose import outbox
    held, filed = [], world.smtp.on_message

    def look(delivery):
        held.append(gates.held(w.ADA))
        if filed:
            filed(delivery)
    world.smtp.on_message = look
    r = app.post("/api/send", {"account": w.ADA, "mode": "new", "to": ["lin@example.test"], "subject": "Linen",
                               "text": "Half each?", "idempotency_key": "k-lockfree"}).json
    assert r["ok"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    assert held == [False]


def test_hwf_the_workers_hold_the_imap_lock_for_their_mail_commands(app, world):
    """The outbox's Sent copy and \\Answered flag, a queued move and a thread summary's body fetch
    reach IMAP from worker threads, and each holds the lock as a request does."""
    from mailmind.bootstrap.compose import outbox
    from mailmind.bootstrap.mailbox import commands
    from mailmind.bootstrap import assistant
    appends, stores = lock_spy(world, "APPEND", user=w.ADA), lock_spy(world, "UID STORE", user=w.ADA)
    r = app.post("/api/send", {"account": w.ADA, "mode": "reply", "original_key": world.key("ada-inbox-02-lunch-confirm.eml"),
                               "to": ["bea@example.test"], "subject": "Re: Lunch", "text": "See you there.",
                               "idempotency_key": "k-gated"}).json
    assert r["ok"]
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    assert (appends, stores) == ([True], [True])

    moves = lock_spy(world, "UID MOVE", user=w.ADA)
    world.server.drop("SELECT", user=w.ADA)
    assert app.post("/api/archive", {**world.copy(RECEIPT)}).json["status"] == "queued"
    commands.drain()
    assert moves == [True]

    pk = world.pk("bea-inbox-01-client.eml")                  # its body was never fetched
    fetches = lock_spy(world, "UID FETCH", user=w.BEA)
    assistant.context.gather([{"pk": pk, "account": w.BEA, "body_text": None, "snippet": "", "body_fetched": 0,
                               "from_name": "Bea", "from_addr": w.BEA, "date_iso": ""}])
    assert fetches == [True]


def while_locked(fn):
    """Run fn in another thread while the IMAP gate is held, as a long mail operation would hold
    it. Returns (finished within 5 s, its result). One that waited for the lock finishes only
    after."""
    out = []
    with gate.held_elsewhere():
        t = threading.Thread(target=lambda: out.append(fn()), name="while-locked", daemon=True)
        t.start()
        t.join(5)
        finished = not t.is_alive()
    t.join(5)
    return finished, (out or [None])[0]


def test_hwf_prefetch_and_the_background_loops_yield_a_held_imap_lock(world, monkeypatch):
    from mailmind.bootstrap.curation import rules
    rules.set_expiry("loom.example.test", 1)
    pk = world.pk("bea-inbox-01-client.eml")                    # its body was never fetched
    n = len(world.server.commands)

    def background():
        got = {"prefetch": mailbox.bodies.message_body(pk, prefetch=True)}
        mailbox.reconciler._auto_reconcile_one(acct())
        curation.expiry.auto_one(acct())
        got["backfill"] = mailbox.syncing.backfill_round()               # yielded, its backlog left for the next round
        return got
    finished, got = while_locked(background)
    assert finished and got == {"prefetch": {"ok": True, "prefetched": False}, "backfill": 8}
    assert world.server.commands[n:] == []                       # none of them touched the server
