"""The body backfill's rounds against the world's fake server: what one failing body, or one account's trouble, may do to
the mail behind it. It may cost that message some waiting, and that account a turn, and no one else anything."""
import time

import pytest

from fixtures import world as w
from gate import held_elsewhere
from mailmind.bootstrap import mailbox
from mailmind.mailbox.application import syncing as syncing_module
from mailmind.mailbox.domain import body_backfill
from mailmind.mailbox.domain.body_backfill import SetAside


class Clock:
    """Stands in for the `time` module inside Syncing: the time moves when a test says, and a sleep returns at once."""
    def __init__(self):
        self.now = time.time()

    def time(self):
        return self.now

    def sleep(self, _seconds):
        pass


@pytest.fixture
def clock(world, monkeypatch):
    monkeypatch.setattr(mailbox.syncing, "_backfill_missed", {})      # world alone resets no module state
    monkeypatch.setattr(mailbox.syncing, "_backfill_resting", {})
    at = Clock()
    monkeypatch.setattr(syncing_module, "time", at)
    return at


def without_body(account=None):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        rows = [dict(r) for r in c.execute("SELECT pk, account, folder, uid FROM messages WHERE body_text IS NULL "
                                           "ORDER BY ts DESC, pk DESC")]
    finally:
        c.close()
    return [r for r in rows if account in (None, r["account"])]


def run_rounds(clock, n):
    """`n` rounds, each when the one before asked for it."""
    for _ in range(n):
        clock.now += mailbox.syncing.backfill_round()


def asked_of(world, account, command="LOGIN"):
    return len(world.server.commands_for(account, command))


def a_poison_message(world):
    """The newest mail with no body, whose FETCH drops the connection every time, as a message a server can't serve does.
    The fake can only tell it by its UID, so what another folder numbers alike gets its body now and is left out of it."""
    from mailmind.bootstrap.mailbox import store
    poison = without_body()[0]
    uid = str(poison["uid"])
    c = store.conn()
    c.execute("UPDATE messages SET body_text='' WHERE account=? AND uid=? AND pk!=?", (poison["account"], poison["uid"], poison["pk"]))
    c.commit()
    c.close()
    world.server.drop("UID FETCH", user=poison["account"], times=None, when=lambda a: str(a[0]) == uid)
    return poison


def test_a_body_that_always_drops_the_connection_holds_back_no_other_mail(world, clock):
    poison = a_poison_message(world)
    run_rounds(clock, 20)
    assert [r["pk"] for r in without_body()] == [poison["pk"]]


def test_a_message_that_keeps_failing_is_asked_for_less_and_less(world, clock):
    poison = a_poison_message(world)

    def asked():
        return sum(1 for x in world.server.commands_for(poison["account"], "UID FETCH") if str(x["args"][0]) == str(poison["uid"]))
    began = clock.now
    mailbox.syncing.backfill_round()
    assert asked() == 1
    clock.now += 61
    mailbox.syncing.backfill_round()
    assert asked() == 2                                           # the same message again: it could still be the server
    while clock.now < began + 6 * 3600:
        run_rounds(clock, 1)
    assert asked() <= 12                                          # not one try a minute for six hours


def test_an_account_that_is_down_holds_back_no_other_account(world, clock):
    down = without_body()[0]["account"]
    world.server.drop("EXAMINE", user=down, times=None)
    run_rounds(clock, 8)
    assert {r["account"] for r in without_body()} == {down}


def test_an_account_that_is_down_is_asked_once_a_minute_and_comes_back_whole(world, clock):
    down = without_body()[0]["account"]
    world.server.drop("EXAMINE", user=down, times=None)
    before = asked_of(world, down)
    mailbox.syncing.backfill_round()
    assert asked_of(world, down) == before + 1                    # it says so once, and asks nothing more this round
    mailbox.syncing.backfill_round()
    assert asked_of(world, down) == before + 1                    # it rests, whoever else has mail waiting
    clock.now += 61
    mailbox.syncing.backfill_round()
    assert asked_of(world, down) == before + 2
    world.server.clear_faults()
    clock.now += 2 * 3600                                         # what was set aside on its way down waits no longer than this
    run_rounds(clock, 8)
    assert without_body() == []


def test_with_every_account_down_the_next_round_is_a_minute_off(world, clock):
    for who in (w.ADA, w.BEA, w.CY):
        world.server.drop("EXAMINE", user=who, times=None)
    assert mailbox.syncing.backfill_round() == 60
    assert mailbox.syncing.backfill_round() == 60                 # all resting: nothing asked, and nothing to do sooner


def test_a_busy_account_holds_up_only_itself(world, clock, monkeypatch):
    monkeypatch.setenv("MAILMIND_WORKERS", "account")
    busy = without_body()[0]["account"]
    since = asked_of(world, busy)
    with held_elsewhere(busy):
        assert mailbox.syncing.backfill_round() == 8
    assert asked_of(world, busy) == since                         # it yields to whoever holds the gate
    fetched = {r["pk"] for r in without_body()}
    others = {w.ADA, w.BEA, w.CY} - {busy}
    assert any(world.server.commands_for(who, "UID FETCH") for who in others)
    assert all(r["pk"] in fetched for r in without_body(busy))
    assert mailbox.syncing._backfill_missed == {} and mailbox.syncing._backfill_resting == {}


def test_one_accounts_backlog_shares_the_round_with_the_rest(world, clock):
    """Ada's mail is all newer than anyone's: she has enough to fill a round, and still it isn't all hers."""
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    c.execute("UPDATE messages SET ts=ts+100000000 WHERE account=?", (w.ADA,))
    c.commit()
    c.close()
    assert len(without_body(w.ADA)) >= 12
    bea, cy = len(without_body(w.BEA)), len(without_body(w.CY))
    mailbox.syncing.backfill_round()
    assert len(without_body(w.BEA)) < bea and len(without_body(w.CY)) < cy


def test_caught_up_the_next_round_is_ten_minutes_off(world, clock):
    run_rounds(clock, 6)
    assert without_body() == []
    assert mailbox.syncing.backfill_round() == 600


# -- the policy alone: values in and values out -----------------------------------

def test_each_account_takes_its_turn_newest_first():
    assert body_backfill.in_turn([["a1", "a2", "a3"], ["b1"], ["c1", "c2"]]) == ["a1", "b1", "c1", "a2", "c2", "a3"]


def test_a_round_stops_at_its_limit_with_every_account_still_served():
    a, b, c = [f"a{i}" for i in range(12)], [f"b{i}" for i in range(12)], [f"c{i}" for i in range(12)]
    round_ = body_backfill.in_turn([a, b, c])
    assert len(round_) == body_backfill.ROUND
    assert round_[:3] == ["a0", "b0", "c0"] and sum(x.startswith("c") for x in round_) == 4


def test_nothing_waiting_is_an_empty_round():
    assert body_backfill.in_turn([]) == [] and body_backfill.in_turn([[], []]) == []


def test_a_body_not_given_waits_the_long_wait_and_forgets_any_lost_connection():
    assert body_backfill.not_given(100) == SetAside(100 + body_backfill.NOT_GIVEN_WAIT)


def test_a_lost_connection_waits_a_minute_then_twice_that_each_time_up_to_the_long_wait():
    one, waits = None, []
    for at in range(12):
        one = body_backfill.connection_lost(one, at)
        waits.append(one.until - at)
    assert waits[:4] == [60, 120, 240, 480]
    assert waits == sorted(waits) and waits[-1] == body_backfill.NOT_GIVEN_WAIT
    assert one.connections_lost == 12


def test_a_wait_that_is_over_lets_the_message_be_asked_for_and_keeps_its_count():
    set_aside = {1: SetAside(50, 3), 2: SetAside(200)}
    assert body_backfill.still_waiting(set_aside, 100) == [2]
    assert body_backfill.still_waiting(set_aside, 50) == [2]        # its time has come
    assert body_backfill.connection_lost(set_aside[1], 100).connections_lost == 4


@pytest.mark.parametrize("going, resting, wait", [(True, False, 8), (True, True, 8), (False, True, 60), (False, False, 600)])
def test_how_long_until_the_next_round(going, resting, wait):
    assert body_backfill.next_round(going=going, resting=resting) == wait
