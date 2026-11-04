"""mailmind.bootstrap.legacy: what `python3 server.py` and the app fixture connect before anything
runs, what a front door starts, and the background jobs each profile schedules."""
import pytest

from mailmind.platform.workers.scheduler import Scheduler


@pytest.fixture
def unwired(monkeypatch):
    """The workers' wiring and the server's jobs as they were, put back after the test."""
    import outbox
    import readmutq
    import realtime
    import server
    for mod, attrs in ((outbox, ("_after_send",)), (readmutq, ("_after_apply",)), (realtime, ("_sync_handler",)),
                       (server, ("JOBS",))):
        for attr in attrs:
            monkeypatch.setattr(mod, attr, getattr(mod, attr))


def test_the_workers_report_back_to_the_server(unwired):
    """That they hold the IMAP lock for their mail commands, test_hwf_http checks at the fake
    server."""
    import outbox
    import readmutq
    import realtime
    import server
    from mailmind.bootstrap import legacy
    legacy.wire(server)
    assert outbox._after_send is server._after_send
    assert readmutq._after_apply is server._after_readmut
    assert realtime._sync_handler is server.sync_guarded



@pytest.mark.parametrize("env", [{"MAILMIND_PROFILE": "read-only"}, {"MAILMIND_NOSYNC": "1"},
                                 {"MAILMIND_NOSYNC": "true"}, {}],
                         ids=["read-only", "nosync", "nosync-true", "full"])
def test_starting_makes_the_schema_wires_the_workers_and_starts_them(env, tmp_path, unwired, monkeypatch,
                                                                     capsys):
    """What both front doors run before they serve. Read-only, only snooze wake starts, since it
    needs no mail server: the outbox and the offline queue wait, and nothing syncs."""
    import realtime
    import server
    import store
    from mailmind.bootstrap import legacy
    from mailmind.platform import settings
    from mailmind.platform.db import migrations
    monkeypatch.delenv("MAILMIND_PROFILE", raising=False)
    monkeypatch.delenv("MAILMIND_NOSYNC", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    read_only = bool(env)
    monkeypatch.setattr(realtime, "start", lambda: spawned.append(realtime.start))
    spawned = []
    with settings.use(settings.Settings(home=str(tmp_path))):
        legacy.start(server, spawn=spawned.append)
        c = store.conn()
        try:
            version = c.execute("PRAGMA user_version").fetchone()[0]
        finally:
            c.close()
    assert version == migrations.MIGRATIONS[-1][0]
    names = [getattr(t, "__name__", "?") for t in spawned]
    if read_only:
        assert names == ["job:snooze"]
    else:
        assert names[:-1] == ["worker_loop", "worker_loop", "job:snooze", "job:sync", "job:backfill",
                              "job:draft-ahead", "job:reconcile", "job:expire"]
        assert spawned[-1] is realtime.start
    assert [j["name"] for j in server.JOBS.snapshot()] == [n.removeprefix("job:") for n in names if "job:" in n]
    out = capsys.readouterr().out
    assert ("auto-sync disabled" in out) == read_only
    assert "[ai] Claude API" in out


def scheduled(monkeypatch, **env):
    """The jobs server.schedule puts on a fresh scheduler under `env`: name, interval and first delay."""
    import server
    for name in ("MAILMIND_PROFILE", "MAILMIND_NOSYNC", "MAILMIND_NO_DRAFT_TICK"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    jobs = Scheduler()
    server.schedule(jobs)
    return [(j.name, j.every, j.first) for j in jobs._jobs.values()]


def test_the_full_profile_schedules_each_job_at_its_pace(monkeypatch):
    assert scheduled(monkeypatch) == [("snooze", 60, 60), ("sync", 300, 0), ("backfill", 600, 45),
                                      ("draft-ahead", 180, 120), ("reconcile", 1800, 120), ("expire", 3600, 180)]


def test_read_only_schedules_snooze_wake_alone(monkeypatch):
    assert scheduled(monkeypatch, MAILMIND_PROFILE="read-only") == [("snooze", 60, 60)]


def test_a_job_switched_off_is_left_out(monkeypatch):
    import server
    monkeypatch.setattr(server, "_RECONCILE_INTERVAL", 0)
    monkeypatch.setattr(server, "_EXPIRE_INTERVAL", 0)
    assert [n for n, *_ in scheduled(monkeypatch, MAILMIND_NO_DRAFT_TICK="1")] == ["snooze", "sync", "backfill"]


def test_a_short_reconcile_cycle_still_waits_30_seconds_between_rounds(monkeypatch):
    import server
    monkeypatch.setattr(server, "_RECONCILE_INTERVAL", 10)
    assert ("reconcile", 30, 10) in scheduled(monkeypatch)


def test_the_sync_jobs_first_round_resolves_the_credentials_and_no_other_does(monkeypatch):
    import accounts
    import server
    calls = []
    monkeypatch.setattr(accounts, "preload_creds", lambda: calls.append("credentials") or 0)
    monkeypatch.setattr(server, "sync_once", lambda: calls.append("sync"))
    sync_round = server.sync_rounds()
    sync_round()
    sync_round()
    assert calls == ["credentials", "sync", "sync"]
