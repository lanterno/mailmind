"""Reconcile reads a folder's UIDs through the pool, so an account with a connection kept doesn't open another. It may wait
only its own short while for any answer, on a kept connection too, whose usual minute it gives back afterwards."""
import time

import pytest

from fixtures import world as w
from mailmind.bootstrap import accounts, mailbox

PATIENCE = 0.5
SHORT = 10      # seconds: past PATIENCE's few waits, short of the 15 a background read waits and a kept connection's minute


@pytest.fixture(autouse=True)
def nothing_kept_after():
    yield
    mailbox.pool.close_all()


@pytest.fixture
def pooled(monkeypatch):
    monkeypatch.setenv("MAILMIND_POOL", w.ADA)


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(mailbox.reconciler, "READ_TIMEOUT", PATIENCE)


def logins(world):
    return len(world.server.commands_for(user=w.ADA, command="LOGIN"))


def reconcile():
    started = time.perf_counter()
    res = mailbox.reconciler.reconcile_account(accounts.get(w.ADA))
    return res, time.perf_counter() - started


def test_two_rounds_on_a_kept_connection_log_in_once_and_never_out(world, pooled):
    before = logins(world)
    assert reconcile()[0]["error"] is None and reconcile()[0]["error"] is None
    assert logins(world) == before + 1 and world.server.commands_for(user=w.ADA, command="LOGOUT") == []


def test_with_the_pool_off_each_round_still_logs_in_and_out(world):
    before = logins(world)
    assert reconcile()[0]["error"] is None and reconcile()[0]["error"] is None
    assert logins(world) == before + 2 and len(world.server.commands_for(user=w.ADA, command="LOGOUT")) == 2


def test_a_kept_connection_that_went_silent_is_given_up_on_within_the_short_wait_and_replaced(world, pooled, quick):
    assert reconcile()[0]["error"] is None
    world.server.stall("NOOP", user=w.ADA)
    try:
        res, took = reconcile()
    finally:
        world.server.release_stalls()
    assert res["error"] is None and took < SHORT           # the check's wait, its LOGOUT's, and a new connection


@pytest.mark.parametrize("command", ["EXAMINE", "UID SEARCH"])
def test_a_kept_connection_silent_on_the_read_itself_prunes_nothing_and_isnt_kept(world, pooled, quick, command):
    assert reconcile()[0]["error"] is None
    stored = reconcile()[0]["stored"]
    before = logins(world)
    world.server.stall(command, user=w.ADA)
    try:
        res, took = reconcile()
    finally:
        world.server.release_stalls()
    assert res["error"] == "TimeoutError" and res["pruned"] == 0 and took < SHORT
    again, _ = reconcile()
    assert again["error"] is None and again["stored"] == stored and logins(world) == before + 1


def test_a_login_that_hangs_on_the_way_to_a_new_connection_waits_only_the_short_while(world, pooled, quick):
    world.server.stall("LOGIN", user=w.ADA)
    try:
        res, took = reconcile()
    finally:
        world.server.release_stalls()
    assert res["error"] and took < SHORT
