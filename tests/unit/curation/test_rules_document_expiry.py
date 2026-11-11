"""`set_expiry` takes a sender or domain as a person types it. A read lower-cases what the rule book holds, so what its own
lower-casing is for shows when a rule is changed in another case than it was set in. test_expire.py has the rule's effect.
"""
import json

import pytest

from mailmind.bootstrap.curation import rules
from mailmind.curation.domain import rulebook
from stored_rows import document

DOMAIN = "notify.example.test"


def as_kept():
    return json.loads(document("rules"))["expire_hours"]


@pytest.mark.parametrize("typed", ["Notify.Example.TEST", "  notify.example.test ", "\tNOTIFY.example.test\n"])
def test_what_is_kept_is_what_a_sender_is_compared_with(data_dir, typed):
    assert rules.set_expiry(typed, 72) == (DOMAIN, 72)
    assert as_kept() == {DOMAIN: 72}
    assert rulebook.expiry_hours("bot@notify.example.test", rules.load()["expire_hours"]) == 72


def test_clearing_in_another_case_clears_the_rule(data_dir):
    rules.set_expiry(DOMAIN, 72)
    rules.set_expiry("NOTIFY.Example.Test", 0)
    assert as_kept() == {}
    assert rulebook.expiry_hours("bot@notify.example.test", rules.load().get("expire_hours", {})) is None


def test_setting_it_again_in_another_case_replaces_the_rule(data_dir):
    rules.set_expiry(DOMAIN, 72)
    rules.set_expiry("Notify.Example.Test", 24)
    assert as_kept() == {DOMAIN: 24}


def test_a_senders_rule_keeps_apart_from_its_domains(data_dir):
    rules.set_expiry(DOMAIN, 72)
    rules.set_expiry("Alerts@Notify.Example.Test", 240)
    assert as_kept() == {DOMAIN: 72, "alerts@notify.example.test": 240}
