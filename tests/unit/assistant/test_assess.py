"""The assess job: triage runs on a lane of its own, outside the sync's single-flight, so a slow
model never holds up a sync. A sync that brings new mail kicks it. While AI is off it tries nothing
at all. Fictional mail and the fake Claude API only."""
from assistant_ports import Model, Triage, message, triage_on
from mailmind.assistant.adapters.disabled import DisabledModel
from mailmind.bootstrap import background


def test_a_kick_for_a_job_this_process_doesnt_run_is_ignored(monkeypatch):
    from mailmind.platform.workers.scheduler import Scheduler
    jobs = Scheduler()
    monkeypatch.setattr(background, "JOBS", jobs)
    background._kick("assess")                         # read-only schedules no assess job
    jobs.add("assess", lambda: None, every=300)
    background._kick("assess")
    assert jobs._jobs["assess"]._wake.is_set()


# ── a round, on fakes behind its ports ───────────────────────────────────────────────────────────

def test_with_ai_off_a_round_tries_nothing():
    triage, tried = Triage(message(n) for n in range(1, 6)), []
    assessments = triage_on(DisabledModel(), triage)
    assessments.summarize_one = tried.append
    assert assessments.round() is None
    assert (tried, triage.said, len(triage.needs_summary(None, 100))) == ([], {}, 5)


def test_a_round_triages_a_batch_and_comes_back_at_once_while_more_wait():
    model, triage = Model(), Triage(message(n) for n in range(1, 6))
    assessments = triage_on(model, triage)
    assessments.ASSESS_BATCH = 3
    assert assessments.round() == 0
    assert len(triage.needs_summary(None, 100)) == 2
    assessments.ASSESS_BATCH = 300
    assert assessments.round() is None
    assert (triage.needs_summary(None, 100), len(model.sent)) == ([], 5)
