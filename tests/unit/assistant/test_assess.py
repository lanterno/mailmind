"""The assess job: triage runs on a lane of its own, outside the sync's single-flight, so a slow
model never holds up a sync. A sync that brings new mail kicks it. While AI is off it tries nothing
at all. Fictional mail and the fake Claude API only."""
from mailmind.bootstrap import background


def test_a_kick_for_a_job_this_process_doesnt_run_is_ignored(monkeypatch):
    from mailmind.platform.workers.scheduler import Scheduler
    jobs = Scheduler()
    monkeypatch.setattr(background, "JOBS", jobs)
    background._kick("assess")                         # read-only schedules no assess job
    jobs.add("assess", lambda: None, every=300)
    background._kick("assess")
    assert jobs._jobs["assess"]._wake.is_set()
