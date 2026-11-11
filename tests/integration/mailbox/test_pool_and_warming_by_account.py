"""An account's ask for a kept connection, as the app reads it from the file and keeps it through an edit on the settings page.
Which accounts the pool keeps a connection for is the unit half's (tests/unit/mailbox/test_pool_and_warming_by_account.py)."""

import pytest

from fixtures import world as w
from mailmind.bootstrap import accounts, mailbox


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


def test_the_key_counts_only_as_true(world):
    edit_by_hand(w.ADA, pool="yes", warm_folders=1)
    ada = accounts.get(w.ADA)
    assert not pooled() and (ada["pool"], ada["warm_folders"]) == (False, False)


def test_an_edit_on_the_settings_page_keeps_the_key(app, world):
    edit_by_hand(w.ADA, pool=True)
    assert app.post("/api/accounts/edit", {"email": w.ADA, "alias": "Ada L."}).status == 200
    assert accounts.get(w.ADA)["pool"] is True
