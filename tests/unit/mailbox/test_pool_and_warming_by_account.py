"""An account can ask in the file for a kept connection, or for more than its INBOX synced, and so last between runs. Neither
variable set and nothing asked keeps everything off, as it always was; a variable that is set decides for the run."""
from types import SimpleNamespace

import pytest

from mailmind.bootstrap import mailbox
from mailmind.mailbox.application.pool import Pool
from mailmind.mailbox.application.warm import Warming

ADA, BEA, CY = "ada@example.test", "bea@example.test", "cy@example.test"


@pytest.fixture(autouse=True)
def nothing_kept_after():
    yield
    mailbox.pool.close_all()


def account(email=ADA, **fields):
    return {"email": email, **fields}


# -- a kept connection --------------------------------------------------------------------------------------------

class _Session:
    def noop(self):
        return True

    def set_timeout(self, seconds):
        pass

    def logout(self):
        pass


class _Directory:
    """The accounts as the file has them, and Bea's server the fragile Bridge."""
    def __init__(self, *accounts):
        self._accounts = list(accounts)

    def accounts(self):
        return self._accounts

    def profile(self, acct):
        return SimpleNamespace(bridge=acct["email"] == BEA, recycle_seconds=600, recycle_uses=200)


@pytest.fixture
def pool_of(monkeypatch):
    """A pool over the accounts given, with neither variable set unless a test sets one."""
    monkeypatch.delenv("MAILMIND_POOL", raising=False)
    made = []

    def make(*accounts):
        made.append(Pool(_Directory(*accounts), lambda acct, **kw: _Session()))
        return made[-1]
    yield make
    for pool in made:
        pool.close_all()


def pooled(pool, acct):
    """Whether two borrows come back with one session, which only a kept connection does."""
    with pool.borrow(acct) as first:
        pass
    with pool.borrow(acct) as second:
        pass
    return first is second


def test_nothing_set_and_nothing_asked_pools_nothing(pool_of):
    pool = pool_of(account())
    assert not pooled(pool, account()) and pool.stats()["enabled"] is False


def test_an_account_that_asks_is_pooled_with_nothing_set(pool_of):
    ada, cy = account(pool=True), account(CY, pool=False)
    pool = pool_of(ada, cy)
    assert pooled(pool, ada) and not pooled(pool, cy)
    assert (pool.stats()["enabled"], pool.stats()["target"]) == (True, "listed")


def test_bridge_is_pooled_when_it_asks_itself(pool_of):
    """As the variable's list takes it: only "all" leaves Bridge out."""
    bea = account(BEA, pool=True)
    assert pooled(pool_of(bea), bea)


@pytest.mark.parametrize("setting", ["0", "off", "someone-else@example.test"])
def test_the_variable_set_decides_the_run_over_what_the_file_says(pool_of, monkeypatch, setting):
    ada = account(pool=True)
    pool = pool_of(ada)
    monkeypatch.setenv("MAILMIND_POOL", setting)
    assert not pooled(pool, ada)


# -- warming ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("fields, env, warmed", [
    ({}, "", False),
    ({"warm_folders": True}, "", True),
    ({"warm_folders": True}, "0", False),
    ({}, "1", True),
    ({}, "someone-else@example.test", False),
])
def test_warming_goes_by_the_variable_if_set_else_by_the_file(monkeypatch, fields, env, warmed):
    directory = SimpleNamespace(accounts=lambda: [account(**fields)])
    warm = Warming(directory, lambda acct: None, lambda *a, **kw: None)
    if env:
        monkeypatch.setenv("MAILMIND_WARM_FOLDERS", env)
    resolved = []
    monkeypatch.setattr(warm, "_roles_for", lambda a: resolved.append(a["email"]) or {})
    warm.warm_tick()
    assert bool(resolved) == warmed


def test_a_warming_tick_reads_no_folder_when_nothing_is_asked(monkeypatch):
    """Off is off: it doesn't look anything up."""
    directory = SimpleNamespace(accounts=lambda: [account()])
    warm = Warming(directory, lambda acct: 1 / 0, lambda *a, **kw: 1 / 0)
    warm.warm_tick()
    assert warm._tick == 0
