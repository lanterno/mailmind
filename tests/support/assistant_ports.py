"""Fakes behind the Assistant's ports, for a use case a unit test builds by hand: a LanguageModel that answers from a queue
and keeps each Prompt it was sent, a UsageLedger in memory, and a unit of work with no database. `calls_on` makes the Calls
every use case asks through, over the real Gateway (and a CostGovernor, when a test hands one), so what reaches the model and
the feature each call is counted under are as the app's."""
import contextlib
import json
import time

from mailmind.assistant.application.calls import Calls
from mailmind.assistant.application.gateway import Gateway
from mailmind.assistant.application.ports import Reply, Usage
from mailmind.assistant.application.status import Status


def reply_of(said, model):
    """What a model answers for `said`: a Reply as it is, a str as its text, anything else as its JSON. An exception is
    raised."""
    if isinstance(said, BaseException):
        raise said
    if isinstance(said, Reply):
        return said
    return Reply(said if isinstance(said, str) else json.dumps(said), model, Usage(), "end_turn")


class Model:
    """A LanguageModel. Each reply a test queued answers one call in turn, as `reply_of` reads it; with none queued, an
    empty object."""
    enabled = True

    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent = []

    def answer(self, *replies):
        self.replies.extend(replies)

    @property
    def prompt(self):
        """The text of the one prompt sent."""
        [sent] = self.sent
        return sent.text

    def complete(self, prompt):
        self.sent.append(prompt)
        return reply_of(self.replies.pop(0) if self.replies else {}, prompt.model)


class Ledger:
    """A UsageLedger in memory: each Spend it was told of."""

    def __init__(self):
        self.spends = []
        self.booked = 0

    @property
    def features(self):
        return [s.feature for s in self.spends]

    def record(self, spend):
        self.spends.append(spend)

    def spent(self, day):
        return sum(s.cost_micros for s in self.spends if s.day == day)

    def reserved(self):
        return self.booked

    def days(self, since):
        return []


class Uow:
    """A unit of work with no database: a block's connection is a stand-in no fake reads. It counts the write blocks."""

    def __init__(self):
        self.writes = 0

    @contextlib.contextmanager
    def read(self):
        yield None

    @contextlib.contextmanager
    def write(self):
        self.writes += 1
        yield None


def calls_on(model, ledger=None, governor=None, clock=time.time):
    """The Calls a use case asks through, over `model` and `ledger` by the real Gateway."""
    gateway = Gateway(lambda: model, Ledger() if ledger is None else ledger, clock=clock, governor=governor)
    status = Status(lambda: gateway, lambda: ("", ""), lambda: "", lambda: governor, lambda: {"waiting": 0},
                    lambda: "")
    return Calls(lambda: gateway, status)
