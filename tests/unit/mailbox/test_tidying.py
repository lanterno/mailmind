"""Tidy on its own: once a day, for someone who turned it on, only what the brief's Tidy offers, and all of it can be taken back."""

import pytest

from fixtures import world as w
from mailmind.mailbox.domain import tidy


# ── the rule ─────────────────────────────────────────────────────────────────────────────────────

ROW = {"folder": "INBOX", "message_key": 1, "uid": 1, "account": w.ADA, "message_id": "<m@example.test>", "importance": 1, "category": "info", "action": ""}


def test_a_row_it_may_take():
    assert tidy.takes(ROW, set())


@pytest.mark.parametrize("change", [{"folder": "Archive"}, {"message_key": None}, {"uid": None}, {"importance": 3}, {"action": "Pay"}, {"category": "newsletter"}])
def test_a_row_it_may_not(change):
    assert not tidy.takes({**ROW, **change}, set())


def test_a_row_someone_took_back():
    assert not tidy.takes(ROW, {(w.ADA, "<m@example.test>")})
