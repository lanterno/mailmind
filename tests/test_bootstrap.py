"""mailmind.bootstrap.legacy.wire: what `python3 server.py` and the app fixture connect before
anything runs."""
import pytest


@pytest.fixture
def unwired(monkeypatch):
    """The workers' wiring as it was, put back after the test."""
    import outbox
    import readmutq
    import realtime
    import summarize_thread
    for mod, attrs in ((outbox, ("_ops_lock", "_after_send")), (readmutq, ("_ops_lock", "_after_apply")),
                       (summarize_thread, ("_ops_lock",)), (realtime, ("_sync_handler",))):
        for attr in attrs:
            monkeypatch.setattr(mod, attr, getattr(mod, attr))


def test_the_workers_share_the_servers_lock_and_report_back_to_it(unwired):
    import outbox
    import readmutq
    import realtime
    import server
    import summarize_thread
    from mailmind.bootstrap import legacy
    legacy.wire(server)
    assert outbox._ops_lock is readmutq._ops_lock is summarize_thread._ops_lock is server._ops_lock
    assert outbox._after_send is server._after_send
    assert readmutq._after_apply is server._after_readmut
    assert realtime._sync_handler is server.sync_guarded



@pytest.mark.parametrize("nosync", ["1", ""], ids=["nosync", "sync"])
def test_starting_makes_the_schema_wires_the_workers_and_starts_them(nosync, tmp_path, unwired, monkeypatch,
                                                                     capsys):
    """What both front doors run before they serve. Under MAILMIND_NOSYNC only the outbox, the
    offline queue and snooze wake start, so queued sends still go out."""
    import outbox
    import readmutq
    import realtime
    import server
    import store
    from mailmind.bootstrap import legacy
    from mailmind.platform import settings
    from mailmind.platform.db import migrations
    monkeypatch.setenv("MAILMIND_NOSYNC", nosync)
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
    assert outbox._ops_lock is readmutq._ops_lock is server._ops_lock
    always = [outbox.worker_loop, readmutq.worker_loop, server.snooze_tick]
    syncing = [server.startup, server.sync_loop, server.body_backfill_tick, server.draft_ahead_tick,
               server.reconcile_loop, server.expire_loop, realtime.start]
    assert spawned == (always if nosync else always + syncing)
    out = capsys.readouterr().out
    assert ("auto-sync disabled" in out) == bool(nosync)
    assert "[ai] Claude API" in out
