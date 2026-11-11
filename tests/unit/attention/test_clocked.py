"""The job that runs every minute (mailmind.attention.application.followups.Clocked): due snoozes wake, then the follow-ups
are brought up to date, and either step failing leaves the other to run."""
import pytest

from mailmind.attention.application.followups import Clocked


class _Step:
    def __init__(self, done, name, fails=False):
        self._done, self._name, self._fails = done, name, fails

    def __call__(self):
        self._done.append(self._name)
        if self._fails:
            raise RuntimeError("ada@example.test: the database is locked")


class _Snoozing:
    def __init__(self, step):
        self.wake_due = step


class _FollowUps:
    def __init__(self, step):
        self.tick = step


def clocked(done, *, snooze_fails=False, followup_fails=False):
    return Clocked(_Snoozing(_Step(done, "wake", snooze_fails)), _FollowUps(_Step(done, "tick", followup_fails)))


def test_snoozes_wake_before_the_follow_ups_are_brought_up_to_date(capsys):
    done = []
    clocked(done).round()
    assert done == ["wake", "tick"]
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("snooze_fails, followup_fails, said", [
    (True, False, "[snooze] tick error: RuntimeError\n"),
    (False, True, "[followup] tick error: RuntimeError\n"),
    (True, True, "[snooze] tick error: RuntimeError\n[followup] tick error: RuntimeError\n"),
], ids=["snooze", "followup", "both"])
def test_a_step_that_fails_is_said_and_the_other_still_runs(capsys, snooze_fails, followup_fails, said):
    done = []
    clocked(done, snooze_fails=snooze_fails, followup_fails=followup_fails).round()
    assert done == ["wake", "tick"]
    assert capsys.readouterr().out == said
