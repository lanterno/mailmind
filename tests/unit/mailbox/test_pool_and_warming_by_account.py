"""An account can ask in the file for a kept connection, or for more than its INBOX synced, and so last between runs. Neither
variable set and nothing asked keeps everything off, as it always was; a variable that is set decides for the run."""
from types import SimpleNamespace

import pytest

from fixtures import world as w
from mailmind.bootstrap import accounts, mailbox
from mailmind.mailbox.application.warm import Warming


def edit_by_hand(email, **fields):
    from mailmind.bootstrap.accounts import config
    with config._write_lock:
        raw = config._raw()
        next(a for a in raw["accounts"] if a["email"] == email).update(fields)
        config.save(raw)


def pooled(email=w.ADA):
    """Whether two borrows come back with one session, which only a kept connection does."""
    with mailbox.pool.borrow(accounts.get(email)) as first:
        pass
    with mailbox.pool.borrow(accounts.get(email)) as second:
        pass
    return first is second


@pytest.fixture(autouse=True)
def nothing_kept_after():
    yield
    mailbox.pool.close_all()


def test_nothing_set_and_nothing_asked_pools_nothing(world):
    assert not pooled() and mailbox.pool.stats()["enabled"] is False


def test_an_account_that_asks_is_pooled_with_nothing_set(world):
    edit_by_hand(w.ADA, pool=True)
    assert pooled() and not pooled(w.CY)
    assert (mailbox.pool.stats()["enabled"], mailbox.pool.stats()["target"]) == (True, "listed")


def test_bridge_is_pooled_when_it_asks_itself(world):
    """As the variable's list takes it: only "all" leaves Bridge out."""
    edit_by_hand(w.BEA, pool=True)
    assert pooled(w.BEA)


@pytest.mark.parametrize("setting", ["0", "off", "someone-else@example.test"])
def test_the_variable_set_decides_the_run_over_what_the_file_says(world, monkeypatch, setting):
    edit_by_hand(w.ADA, pool=True)
    monkeypatch.setenv("MAILMIND_POOL", setting)
    assert not pooled()


def test_the_key_counts_only_as_true(world):
    edit_by_hand(w.ADA, pool="yes", warm_folders=1)
    ada = accounts.get(w.ADA)
    assert not pooled() and (ada["pool"], ada["warm_folders"]) == (False, False)


def test_an_edit_on_the_settings_page_keeps_the_key(app, world):
    edit_by_hand(w.ADA, pool=True)
    assert app.post("/api/accounts/edit", {"email": w.ADA, "alias": "Ada L."}).status == 200
    assert accounts.get(w.ADA)["pool"] is True


def account(**fields):
    return {"email": "ada@example.test", **fields}


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
