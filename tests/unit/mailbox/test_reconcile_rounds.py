"""The ghost pruner's job (Reconciler.reconcile_rounds): each round takes one account, in turn, and says when the next
is due, so a full cycle spans MAILMIND_RECONCILE_SECS. One account failing is said, and the cycle goes on past it."""
from types import SimpleNamespace

from mailmind.mailbox.application.reconcile import Reconciler
from mailmind.mailbox.domain.prune import EVERY_AT_LEAST

ADA, BEA, CY = "ada@example.test", "bea@example.test", "cy@example.test"


def reconciler(monkeypatch, emails, *, failing=(), interval=900):
    accounts = SimpleNamespace(accounts=lambda: [{"email": e} for e in emails])
    r = Reconciler(None, None, accounts, None, None, None)
    monkeypatch.setattr(r, "_RECONCILE_INTERVAL", interval)
    pruned = []

    def one(acct):
        pruned.append(acct["email"])
        if acct["email"] in failing:
            raise RuntimeError(f"{acct['email']}: the database is locked")
    monkeypatch.setattr(r, "_auto_reconcile_one", one)
    return r, pruned


def test_each_round_takes_the_next_account_in_turn(monkeypatch):
    r, pruned = reconciler(monkeypatch, [ADA, BEA, CY])
    round_ = r.reconcile_rounds()
    assert [round_() for _ in range(5)] == [300] * 5
    assert pruned == [ADA, BEA, CY, ADA, BEA]


def test_each_job_keeps_its_own_place_in_the_turn(monkeypatch):
    r, pruned = reconciler(monkeypatch, [ADA, BEA])
    first, second = r.reconcile_rounds(), r.reconcile_rounds()
    first(), first(), second()
    assert pruned == [ADA, BEA, ADA]


def test_an_account_that_fails_is_said_and_the_next_round_takes_the_next(monkeypatch, capsys):
    r, pruned = reconciler(monkeypatch, [ADA, BEA], failing={ADA})
    round_ = r.reconcile_rounds()
    assert [round_(), round_()] == [450, 450]
    assert pruned == [ADA, BEA]
    assert capsys.readouterr().out == "[reconcile] loop error: RuntimeError\n"


def test_with_no_accounts_a_round_does_nothing_and_comes_back_after_the_whole_interval(monkeypatch):
    r, pruned = reconciler(monkeypatch, [])
    assert r.reconcile_rounds()() == 900 and pruned == []


def test_however_many_accounts_a_round_comes_no_sooner_than_the_floor(monkeypatch):
    r, _ = reconciler(monkeypatch, [f"u{i}@example.test" for i in range(1000)], interval=60)
    assert r.reconcile_rounds()() == EVERY_AT_LEAST
