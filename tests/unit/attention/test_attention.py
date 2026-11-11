"""The questions a verdict answers, and the SQL that asks them over stored rows: the two must agree."""

import pytest

from mailmind.kernel import attention
from mailmind.platform.db import driver

IMPORTANCE = [None, 1, 2, 3, 4, 5]
CATEGORY = [None, "action-needed", "personal", "work-job", "finance", "calendar", "info", "other"]
ACTION = [None, "", "   ", "Reply to Lin"]
GRID = [(i, c, a) for i in IMPORTANCE for c in CATEGORY for a in ACTION]


@pytest.mark.parametrize("importance, category, action, answer", [
    (5, "info", "", True),
    (4, "other", None, True),
    (3, "action-needed", "", True),
    (1, "info", "Pay the invoice", True),                # a next step alone is enough
    (1, "info", "   ", False),                           # a blank one is no next step
    (3, "personal", "", False),
    (None, None, None, False),                           # no verdict yet
])
def test_needs_you(importance, category, action, answer):
    assert attention.needs_you(importance, category, action) is answer


@pytest.mark.parametrize("importance, category, answer", [
    (4, "info", True),
    (1, "action-needed", True),
    (3, "work-job", False),
    (None, None, False),
])
def test_reply_worthy(importance, category, answer):
    assert attention.reply_worthy(importance, category) is answer


def test_a_next_step_alone_doesnt_spend_a_draft():
    assert attention.needs_you(1, "info", "Read it") and not attention.reply_worthy(1, "info")


@pytest.mark.parametrize("importance, category, action, answer", [
    (3, "work-job", "Send the slides", True),
    (1, "action-needed", "Send the slides", True),
    (2, "work-job", "Send the slides", False),           # a low-priority nudge
    (5, "action-needed", "", False),                     # nothing to put on the agenda
    (5, "action-needed", "  ", False),
])
def test_commitment_worthy(importance, category, action, answer):
    assert attention.commitment_worthy(importance, category, action) is answer


@pytest.mark.parametrize("importance, category, action, answer", [
    (1, "info", "", True),
    (2, "other", None, True),
    (None, "info", "", True),                            # filing is reversible
    (3, "info", "", False),
    (1, "personal", "", False),
    (1, "info", "Read it", False),
    (1, "action-needed", "", False),
    (1, None, "", False),                                # no verdict yet: nothing to go on
])
def test_safe_to_file(importance, category, action, answer):
    assert attention.safe_to_file(importance, category, action) is answer


def test_nothing_that_needs_you_is_safe_to_file():
    for i, c, a in GRID:
        assert not (attention.needs_you(i, c, a) and attention.safe_to_file(i, c, a)), (i, c, a)


def test_everything_on_the_agenda_needs_you():
    for i, c, a in GRID:
        assert not attention.commitment_worthy(i, c, a) or attention.needs_you(i, c, a), (i, c, a)


@pytest.mark.parametrize("alias", ["s", ""])
def test_the_sql_answers_as_the_python_does(alias):
    db = driver.connect(":memory:")
    db.execute("CREATE TABLE assessments(importance, category, action)")
    table = "assessments s" if alias else "assessments"
    for row in GRID:
        db.execute("DELETE FROM assessments")
        db.execute("INSERT INTO assessments VALUES(?,?,?)", row)
        i, c, a = row
        got = db.execute(f"SELECT 1 FROM {table} WHERE {attention.needs_you_sql(alias)}").fetchone()
        assert bool(got) == attention.needs_you(i, c, a), row
        got = db.execute(f"SELECT 1 FROM {table} WHERE {attention.reply_worthy_sql(alias)}").fetchone()
        assert bool(got) == attention.reply_worthy(i, c), row
