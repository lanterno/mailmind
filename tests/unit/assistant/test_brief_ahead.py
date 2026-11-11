"""The morning's brief, written ahead of the first open: in the morning, once triage has read the last day's mail, once a day,
and within the cost governor. Briefs on stored mail of its own, a model of its own and the real governor, on a clock that
starts as the app does, at Monday noon."""
from datetime import datetime, timedelta, timezone

import pytest
import time_machine

from assistant_ports import Ledger, Model, Said, Uow, calls_on
from mailmind.assistant.application.brief import Briefs
from mailmind.assistant.application.governor import CostGovernor
from mailmind.assistant.application.ports import ModelError

ADA = "ada@example.test"
STARTED = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)      # when the app started, with someone there
MORNING = datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc)
CLIENT = {"pk": 3, "message_key": 30, "folder": "INBOX", "message_id": "<contract@client.example.test>", "from_name": "Ivo",
          "from_addr": "ivo@client.example.test", "subject": "The contract", "summary": "Wants an answer.", "importance": 5,
          "category": "action-needed", "action": "Reply today"}


class Mail:
    """Triaged mail, for a brief to be made from."""

    def brief_rows(self, c, accounts, n):
        return [CLIENT][:n]

    def copy_now(self, c, key, folder):
        return {"folder": folder, "uid": 7} if key == CLIENT["message_key"] else None


class Ahead:
    """Briefs, and what it was told and kept, built at STARTED and then moved to the morning."""

    def __init__(self, clock):
        self.clock, self.model, self.ledger, self.said = clock, Model(), Ledger(), Said()
        self.governor = CostGovernor(spent=lambda: 0, limit=lambda: 15_000_000)
        self.briefs = Briefs(calls_on(self.model, self.ledger, self.governor), self.said, Uow(), Mail())
        clock.move_to(MORNING)

    def round(self):
        self.briefs.write_ahead({ADA})

    def written(self):
        return [b["narrative"] for b in self.said.briefs.values()]


@pytest.fixture
def ahead():
    with time_machine.travel(STARTED, tick=False) as clock:
        yield Ahead(clock)


def not_triaged(ahead, age):
    """A message triage hasn't read yet, `age` before the morning."""
    ahead.said.waiting.append({"pk": 9, "ts": int((MORNING - age).timestamp())})


def test_a_brief_already_written_today_is_left_alone(ahead):
    ahead.round()
    ahead.round()
    assert len(ahead.model.sent) == 1


@pytest.mark.parametrize("hour", [0, 4, 12, 23])
def test_outside_the_morning_nothing_is_written(ahead, hour):
    ahead.clock.move_to(MORNING.replace(hour=hour))
    ahead.round()
    assert (ahead.model.sent, ahead.written()) == ([], [])


@pytest.mark.parametrize("hour", [5, 11])
def test_the_morning_is_from_five_until_noon_and_counted_as_written_ahead(ahead, hour):
    ahead.model.answer({"narrative": "Ivo wants an answer.", "groups": []})
    ahead.clock.move_to(MORNING.replace(hour=hour))
    ahead.round()
    assert (ahead.written(), ahead.ledger.features) == (["Ivo wants an answer."], ["brief-ahead"])


def test_it_waits_for_triage_to_read_the_last_days_mail(ahead):
    not_triaged(ahead, timedelta(hours=2))
    ahead.round()
    assert ahead.model.sent == []


def test_an_older_backlog_doesnt_hold_it_up(ahead):
    not_triaged(ahead, timedelta(days=3))
    ahead.round()
    assert len(ahead.model.sent) == 1


def test_after_days_away_nothing_is_written_until_someone_is_back(ahead):
    ahead.clock.move_to(MORNING + timedelta(days=2))
    ahead.round()
    assert (ahead.model.sent, ahead.written()) == ([], [])
    ahead.governor.saw_someone()
    ahead.round()
    assert len(ahead.written()) == 1


def test_a_day_off_leaves_it_on(ahead):
    ahead.clock.move_to(MORNING + timedelta(days=1))       # nobody has been here since the day before yesterday's noon
    ahead.round()
    assert len(ahead.written()) == 1


def test_a_failed_call_waits_for_the_next_round(ahead):
    ahead.model.answer(ModelError("bad request", 400))
    ahead.round()
    assert ahead.written() == []
    ahead.round()
    assert len(ahead.written()) == 1
