"""The connection pool over plain stand-ins: which accounts it serves, what MAILMIND_POOL unset leaves as
it was, and each account's provider saying when its connection is replaced."""
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import time_machine

from mailmind.mailbox.application.pool import Pool

ADA, BRIDGE = "ada@example.test", "bridge@example.test"


class Session:
    def __init__(self):
        self.noops = 0
        self.logouts = 0
        self.noop_answer = True
        self.timeouts = []
        self.at_noop = []

    def set_timeout(self, seconds):
        self.timeouts.append(seconds)

    def noop(self):
        self.noops += 1
        self.at_noop.append(self.timeouts[-1:])
        if isinstance(self.noop_answer, Exception):
            raise self.noop_answer
        return self.noop_answer

    def logout(self):
        self.logouts += 1


class Accounts:
    def accounts(self):
        return []

    def profile(self, acct):
        if acct["email"] == BRIDGE:
            return SimpleNamespace(bridge=True, recycle_seconds=90, recycle_uses=40)
        return SimpleNamespace(bridge=False, recycle_seconds=600, recycle_uses=200)


@pytest.fixture
def opened():
    return []


@pytest.fixture
def pool(opened):
    def open_session(acct):
        opened.append(Session())
        return opened[-1]
    return Pool(Accounts(), open_session)


@pytest.fixture
def clock():
    with time_machine.travel(datetime(2026, 11, 2, 12, tzinfo=UTC), tick=False) as traveller:
        yield traveller


def borrow(pool, email=ADA):
    with pool.borrow({"email": email}) as s:
        return s


def test_unset_each_borrow_is_a_fresh_session_logged_out_after(pool, opened, monkeypatch):
    monkeypatch.delenv("MAILMIND_POOL", raising=False)
    first, second = borrow(pool), borrow(pool)
    assert first is not second and opened == [first, second]
    assert (first.logouts, first.noops, second.logouts) == (1, 0, 1)
    assert pool.stats() == {"enabled": False, "target": "",
                            "counters": {"created": 0, "reused": 0, "validate_fail": 0, "discard_dirty": 0}, "slots": []}


def test_unset_a_borrow_that_raises_is_logged_out_too(pool, opened, monkeypatch):
    monkeypatch.delenv("MAILMIND_POOL", raising=False)
    with pytest.raises(ValueError):
        with pool.borrow({"email": ADA}):
            raise ValueError("a read went wrong")
    assert opened[0].logouts == 1


@pytest.mark.parametrize("setting, ada, bridge", [("1", True, False), ("all", True, False), ("on", True, False),
                                                  (ADA, True, False), (f"{ADA}, {BRIDGE}", True, True),
                                                  (BRIDGE, False, True), ("no@example.test", False, False)])
def test_which_accounts_it_keeps_a_connection_for(pool, monkeypatch, setting, ada, bridge):
    """All but the fragile Bridge, or a list, taken as it stands."""
    monkeypatch.setenv("MAILMIND_POOL", setting)
    assert (borrow(pool, ADA) is borrow(pool, ADA), borrow(pool, BRIDGE) is borrow(pool, BRIDGE)) == (ada, bridge)


@pytest.mark.parametrize("setting", ["0", "off", "False", "ture"])
def test_an_off_word_or_a_typo_keeps_nothing_and_says_it_is_off(pool, opened, monkeypatch, setting):
    monkeypatch.setenv("MAILMIND_POOL", setting)
    assert borrow(pool, ADA) is not borrow(pool, ADA) and borrow(pool, BRIDGE) is not borrow(pool, BRIDGE)
    assert (pool.stats()["enabled"], pool.stats()["target"], pool.stats()["slots"]) == (False, "", [])


def test_a_kept_session_is_checked_before_each_reuse_and_stays_logged_in(pool, opened, monkeypatch):
    monkeypatch.setenv("MAILMIND_POOL", ADA)
    first = borrow(pool)
    assert (first.noops, first.logouts) == (0, 0)
    assert borrow(pool) is first and borrow(pool) is first
    assert (first.noops, first.logouts, len(opened)) == (2, 0, 1)


@pytest.mark.parametrize("answer", [False, OSError("gone")], ids=["says-no", "raises"])
def test_a_session_that_fails_its_check_is_replaced(pool, opened, monkeypatch, answer):
    monkeypatch.setenv("MAILMIND_POOL", ADA)
    first = borrow(pool)
    first.noop_answer = answer
    second = borrow(pool)
    assert second is not first and first.logouts == 1
    assert pool.stats()["counters"] == {"created": 2, "reused": 0, "validate_fail": 1, "discard_dirty": 0}


@pytest.mark.parametrize("email, most", [(ADA, 200), (BRIDGE, 40)])
def test_a_session_is_replaced_after_the_providers_uses_unchecked(pool, opened, monkeypatch, email, most):
    monkeypatch.setenv("MAILMIND_POOL", email)
    first = borrow(pool, email)
    for _ in range(most - 1):
        assert borrow(pool, email) is first
    noops = first.noops
    assert borrow(pool, email) is not first
    assert (first.logouts, first.noops, len(opened)) == (1, noops, 2)
    assert pool.stats()["counters"] == {"created": 2, "reused": most - 1, "validate_fail": 0, "discard_dirty": 0}


@pytest.mark.parametrize("email, most", [(ADA, 600), (BRIDGE, 90)])
def test_a_session_is_replaced_past_the_providers_age(pool, opened, monkeypatch, clock, email, most):
    monkeypatch.setenv("MAILMIND_POOL", email)
    first = borrow(pool, email)
    clock.shift(timedelta(seconds=most))
    assert borrow(pool, email) is first
    clock.shift(timedelta(seconds=1))
    assert borrow(pool, email) is not first and first.logouts == 1


def test_a_borrow_that_raises_poisons_the_slot(pool, opened, monkeypatch):
    monkeypatch.setenv("MAILMIND_POOL", ADA)
    first = borrow(pool)
    with pytest.raises(ValueError):
        with pool.borrow({"email": ADA}):
            raise ValueError("a read went wrong")
    assert first.logouts == 1
    assert pool.stats()["slots"] == [{"open": False, "uses": 2, "age": None}]
    assert borrow(pool) is not first
    assert pool.stats()["counters"] == {"created": 2, "reused": 1, "validate_fail": 0, "discard_dirty": 1}


def test_stats_say_how_each_slot_stands(pool, monkeypatch, clock):
    monkeypatch.setenv("MAILMIND_POOL", ADA)
    borrow(pool)
    borrow(pool)
    clock.shift(timedelta(seconds=7.5))
    assert pool.stats() == {"enabled": True, "target": "listed",
                            "counters": {"created": 1, "reused": 1, "validate_fail": 0, "discard_dirty": 0},
                            "slots": [{"open": True, "uses": 2, "age": 7.5}]}


def test_close_all_logs_each_kept_session_out(pool, opened, monkeypatch):
    monkeypatch.setenv("MAILMIND_POOL", f"{ADA},{BRIDGE}")
    borrow(pool, ADA)
    borrow(pool, BRIDGE)
    pool.close_all()
    assert [s.logouts for s in opened] == [1, 1]
    assert [s["open"] for s in pool.stats()["slots"]] == [False, False]
    pool.close_all()
    assert [s.logouts for s in opened] == [1, 1]


def test_one_account_lends_to_one_borrower_at_a_time_and_holds_up_no_other(pool, monkeypatch):
    monkeypatch.setenv("MAILMIND_POOL", f"{ADA},{BRIDGE}")
    second_in = threading.Event()

    def second():
        with pool.borrow({"email": ADA}):
            second_in.set()
    t = threading.Thread(target=second, daemon=True)
    with pool.borrow({"email": ADA}):
        t.start()
        assert not second_in.wait(0.1)
        assert borrow(pool, BRIDGE) is not None
    t.join(5)
    assert second_in.is_set()


@pytest.fixture
def asked():
    return []


@pytest.fixture
def timing_pool(asked):
    def open_session(acct, **kw):
        asked.append(kw)
        return Session()
    return Pool(Accounts(), open_session)


def borrow_within(pool, seconds):
    with pool.borrow({"email": ADA}, timeout=seconds) as s:
        return s


def test_unset_a_borrow_with_a_timeout_opens_with_it(timing_pool, asked, monkeypatch):
    monkeypatch.delenv("MAILMIND_POOL", raising=False)
    s = borrow_within(timing_pool, 15)
    assert asked == [{"timeout": 15}] and (s.timeouts, s.logouts) == ([], 1)


def test_unset_a_borrow_naming_no_timeout_asks_for_none(timing_pool, asked, monkeypatch):
    monkeypatch.delenv("MAILMIND_POOL", raising=False)
    with timing_pool.borrow({"email": ADA}):
        pass
    assert asked == [{}]


def test_a_new_kept_session_opens_with_the_timeout_and_goes_back_to_the_usual_minute(timing_pool, asked, monkeypatch):
    monkeypatch.setenv("MAILMIND_POOL", ADA)
    s = borrow_within(timing_pool, 15)
    assert asked == [{"timeout": 15}] and s.timeouts == [Pool.KEPT_TIMEOUT] and s.logouts == 0


def test_a_kept_session_is_checked_within_the_timeout_and_goes_back_after(timing_pool, monkeypatch):
    monkeypatch.setenv("MAILMIND_POOL", ADA)
    kept = borrow_within(timing_pool, 15)
    assert borrow_within(timing_pool, 15) is kept
    assert kept.at_noop == [[15]] and kept.timeouts == [Pool.KEPT_TIMEOUT, 15, Pool.KEPT_TIMEOUT]


def test_a_borrow_naming_no_timeout_leaves_a_kept_session_as_it_is(timing_pool, monkeypatch):
    monkeypatch.setenv("MAILMIND_POOL", ADA)
    with timing_pool.borrow({"email": ADA}) as kept:
        pass
    with timing_pool.borrow({"email": ADA}) as again:
        pass
    assert again is kept and kept.timeouts == []


def test_a_borrow_that_raises_isnt_kept_whatever_its_timeout(timing_pool, monkeypatch):
    monkeypatch.setenv("MAILMIND_POOL", ADA)
    with pytest.raises(ValueError):
        with timing_pool.borrow({"email": ADA}, timeout=15) as s:
            raise ValueError("a read went wrong")
    assert s.logouts == 1 and s.timeouts == [] and borrow_within(timing_pool, 15) is not s
