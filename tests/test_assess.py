"""The assess job: triage runs on a lane of its own, outside the sync's single-flight, so a slow
model never holds up a sync. A sync that brings new mail kicks it. While AI is off it tries nothing
at all. Fictional mail and the fake Claude API only."""
from fixtures import world as w
from mailmind.bootstrap.assistant import answers
from mailmind.bootstrap import mailbox
from mailmind.bootstrap import assistant
from mailmind.bootstrap import background


def unsummarized():
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return len(answers.needs_summary(c, 10_000))
    finally:
        c.close()


def test_with_ai_off_a_round_tries_nothing(app, world, anthropic_api, monkeypatch):
    from mailmind.bootstrap import assistant
    tried = []
    monkeypatch.setattr(assistant.triage, "summarize_one", tried.append)
    waiting = unsummarized()
    assert waiting > 0
    assert assistant.triage.round() is None
    assert (tried, anthropic_api.requests, unsummarized()) == ([], [], waiting)


def test_a_round_triages_a_batch_and_comes_back_at_once_while_more_wait(app, world, ai, monkeypatch):
    waiting = unsummarized()
    monkeypatch.setattr(assistant.triage, "ASSESS_BATCH", 3)
    assert assistant.triage.round() == 0
    assert unsummarized() == waiting - 3
    monkeypatch.setattr(assistant.triage, "ASSESS_BATCH", 300)
    assert assistant.triage.round() is None
    assert unsummarized() == 0 and len(ai.requests) == waiting


def test_a_sync_with_new_mail_kicks_triage_and_asks_nothing_itself(app, world, ai, monkeypatch):
    kicked = []
    monkeypatch.setattr(background, "_kick", kicked.append)
    world.server.deliver(w.ADA, "INBOX", w.variant("ada-inbox-02-lunch-confirm.eml",       # on the server only
                                                   Message_ID="<lunch-5@example.test>",
                                                   Subject="Re: Lunch, a table for three"))
    mailbox.syncing.sync_guarded({w.ADA})
    assert (kicked, mailbox.syncing._sync["result"], ai.requests) == (["assess"], "1 new", [])
    mailbox.syncing.sync_guarded({w.ADA})
    assert (kicked, mailbox.syncing._sync["result"]) == (["assess"], "0 new")


def test_a_kick_for_a_job_this_process_doesnt_run_is_ignored(monkeypatch):
    from mailmind.platform.workers.scheduler import Scheduler
    jobs = Scheduler()
    monkeypatch.setattr(background, "JOBS", jobs)
    background._kick("assess")                         # read-only schedules no assess job
    jobs.add("assess", lambda: None, every=300)
    background._kick("assess")
    assert jobs._jobs["assess"]._wake.is_set()
