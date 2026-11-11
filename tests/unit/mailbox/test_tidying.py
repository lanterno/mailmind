"""Tidy on its own: once a day, for someone who turned it on, only what the brief's Tidy offers, and all of it can be taken back.
Here the rule a row is judged by (mailmind.mailbox.domain.tidy) and when a run files (mailmind.mailbox.application.tidying.Tidying,
on a stand-in for the store and filing); the job, the AI's verdicts and Undo through the app are the integration half's."""
import contextlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import time_machine

from fixtures import world as w
from mailmind.mailbox.application.tidying import Tidying
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


# ── when a run files ─────────────────────────────────────────────────────────────────────────────

MORNING = datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc)
SAFE = {**ROW, "message_key": 1, "uid": 11, "message_id": "<lunch@example.test>"}
TOO = {**ROW, "message_key": 2, "uid": 12, "message_id": "<confirm@example.test>"}


class _Mail:
    """What a run reads of the store (the digest's rows, and whether a run already filed under a mutation id) and the filing
    it asks for, which keeps each command under the run's mutation id as the command path does."""

    def __init__(self, *rows):
        self.rows, self.ran, self.filed = list(rows), set(), []

    def command_ran_under(self, c, mutation_id):
        return mutation_id in self.ran

    def digest(self, c, limit=300):
        return self.rows

    def archives_undone(self, c):
        return set()

    def file_many(self, body):
        self.filed.append((body["undo_group"], [r["key"] for r in body["rows"]]))
        self.ran.add(body["mutation_id"])


class _Uow:
    @contextlib.contextmanager
    def read(self):
        yield "c"


def round_at(mail, when):
    with time_machine.travel(when, tick=False):
        Tidying(_Uow(), mail, mail, None).round()


def test_it_runs_once_a_day():
    mail = _Mail(SAFE)
    round_at(mail, MORNING)
    mail.rows.append(TOO)                                   # safe to file only later that day
    round_at(mail, MORNING + timedelta(hours=3))
    assert mail.filed == [("tidy-2026-11-03", [1])]


def test_the_next_day_it_runs_again():
    mail = _Mail(SAFE)
    round_at(mail, MORNING)
    mail.rows = [TOO]
    round_at(mail, MORNING + timedelta(days=1))
    assert mail.filed == [("tidy-2026-11-03", [1]), ("tidy-2026-11-04", [2])]


def test_nothing_to_file_doesnt_use_up_the_day():
    mail = _Mail()
    round_at(mail, MORNING)
    mail.rows.append(SAFE)
    round_at(mail, MORNING + timedelta(minutes=5))
    assert mail.filed == [("tidy-2026-11-03", [1])]


@pytest.mark.parametrize("hour, files", [(5, False), (6, True)])
def test_it_waits_for_the_day_to_start(hour, files):
    mail = _Mail(SAFE)
    round_at(mail, MORNING.replace(hour=hour))
    assert bool(mail.filed) is files


# ── what the brief's strip shows (Tidying.latest) ────────────────────────────────────────────────

def run(group, *subjects):
    return {"kind": "bulk", "group": group, "children": [{"subject": s, "who": "Lin Mori"} for s in subjects]}


def latest(*entries):
    return Tidying(None, None, None, SimpleNamespace(entries=lambda: list(entries))).latest()


def test_the_strip_shows_the_newest_run_still_on_the_stack_newest_first():
    got = latest(run("tidy-2026-11-03", "Lunch"), run("tidy-2026-11-04", "Receipt", "Venue"), run("bulk:mine", "Mine"),
                 {"kind": "move", "subject": "Mine too"})
    assert got == {"ok": True, "group": "tidy-2026-11-04", "filed": 2,
                   "items": [{"subject": "Venue", "who": "Lin Mori"}, {"subject": "Receipt", "who": "Lin Mori"}]}


def test_with_no_run_on_the_stack_the_strip_shows_none():
    assert latest(run("bulk:mine", "Mine")) == {"ok": True, "group": None, "filed": 0, "items": []}
