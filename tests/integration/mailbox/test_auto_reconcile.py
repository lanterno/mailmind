"""The ghost pruner's turn at one account (Reconciler._auto_reconcile_one), against the world's server: a message gone from
it goes from the store, the audit trail says so, and an open list hears it. A user op taking the gate between two folders
leaves the rest for the next turn."""
import json

from fixtures import world as w
from mailmind.bootstrap import accounts
from mailmind.platform import settings, sse
from mailmind.platform.workers import gates

RECEIPT = "ada-inbox-05-receipt.eml"


def heard(monkeypatch):
    events = []
    monkeypatch.setattr(sse, "publish", lambda topic, data=None: events.append((topic, data)))
    return events


def audit():
    path = settings.current().reconcile_audit
    try:
        return [json.loads(line) for line in open(path).read().splitlines()]
    except FileNotFoundError:
        return []


def test_a_message_gone_from_the_server_goes_from_the_store_and_is_said(world, monkeypatch):
    from mailmind.bootstrap.mailbox import reconciler
    events = heard(monkeypatch)
    with world.server.lock:                                       # deleted in another client
        world.server.mailbox(w.ADA, "INBOX").messages.pop(world.uid(RECEIPT))
    reconciler._auto_reconcile_one(accounts.get(w.ADA))
    assert world.pk(RECEIPT) is None
    assert events == [("reconcile", {"account": w.ADA, "pruned": 1})]
    [line] = audit()
    assert {k: line[k] for k in ("kind", "auto", "account", "pruned")} == \
        {"kind": "reconcile", "auto": True, "account": w.ADA, "pruned": 1}


def test_with_nothing_gone_nothing_is_said(world, monkeypatch):
    from mailmind.bootstrap.mailbox import reconciler
    events = heard(monkeypatch)
    reconciler._auto_reconcile_one(accounts.get(w.ADA))
    assert events == [] and audit() == []


def test_a_list_that_cant_hear_it_stops_nothing(world, monkeypatch):
    from mailmind.bootstrap.mailbox import reconciler

    def deaf(topic, data=None):
        raise RuntimeError("no one is listening")
    monkeypatch.setattr(sse, "publish", deaf)
    with world.server.lock:
        world.server.mailbox(w.ADA, "INBOX").messages.pop(world.uid(RECEIPT))
    reconciler._auto_reconcile_one(accounts.get(w.ADA))
    assert world.pk(RECEIPT) is None and len(audit()) == 1


def test_a_user_op_between_two_folders_leaves_the_rest_for_the_next_turn(world, monkeypatch):
    from mailmind.bootstrap.mailbox import reconciler
    folders = reconciler.reconciled_folders(w.ADA)
    assert len(folders) > 1
    real, turns = gates.run, []

    def busy_after_the_first(account, fn, **kw):
        turns.append(account)
        if len(turns) > 1:
            raise gates.Busy()
        return real(account, fn, **kw)
    monkeypatch.setattr(gates, "run", busy_after_the_first)
    out = reconciler._reconcile_folders(accounts.get(w.ADA), priority=gates.Priority.MAINTENANCE, wait=False)
    assert (out["folders"], turns) == (1, [w.ADA, w.ADA])
