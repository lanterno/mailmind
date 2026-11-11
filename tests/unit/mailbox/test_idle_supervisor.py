"""IDLE's supervisor (realtime.reconcile): the workers follow the configuration as it changes. An
account added is watched, one removed or disabled stops being watched at once, and one now reached
another way gets a fresh worker; a change that doesn't touch how it is reached leaves the worker
be. The account events it hears come from config (mailmind.accounts.domain.events). Each test has a supervisor of its own,
and sees its workers through the sessions they open."""

import pytest


DEE = "dee@example.test"


def test_the_configuration_says_what_happened_to_each_account(data_dir):
    from mailmind.bootstrap.accounts import config
    from mailmind.accounts.domain import events
    from mailmind.platform import bus
    heard = []
    ends = [bus.subscribe(t, heard.append) for t in (events.AccountAdded, events.AccountChanged, events.AccountRemoved)]
    try:
        config.add_account({"email": DEE, "type": "imap", "host": "mail.example.test",
                            "credential": {"style": "keychain", "value": "app-password"}})
        config.update_account(DEE, {"alias": "Dee"})
        assert config.remove_account(DEE)
        with pytest.raises(ValueError):
            config.add_account({"email": "no-at-sign", "credential": {"style": "keychain", "value": "x"}})
        assert config.update_account(DEE, {"alias": "Dee"}) is None
        assert config.remove_account(DEE) is False
    finally:
        for end in ends:
            end()
    assert heard == [events.AccountAdded(DEE), events.AccountChanged(DEE), events.AccountRemoved(DEE)]
