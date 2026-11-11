"""An account can ask in the file for a kept connection, or for more than its INBOX synced, and so last between runs. Neither
variable set and nothing asked keeps everything off, as it always was; a variable that is set decides for the run."""
from types import SimpleNamespace

import pytest

from mailmind.bootstrap import mailbox
from mailmind.mailbox.application.warm import Warming


@pytest.fixture(autouse=True)
def nothing_kept_after():
    yield
    mailbox.pool.close_all()


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
