"""mailmind.bootstrap.legacy.wire: what `python3 server.py` and the app fixture connect before
anything runs."""
import pytest


@pytest.fixture
def unwired(monkeypatch):
    """The workers' wiring as it was, put back after the test."""
    import outbox
    import readmutq
    import realtime
    for mod, attrs in ((outbox, ("_after_send",)), (readmutq, ("_after_apply",)), (realtime, ("_sync_handler",))):
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

    def started(word):
        return any(word in n for n in names)
    background = ("sync", "backfill", "draft", "reconcile", "expire")
    if read_only:
        assert started("snooze") and not started("worker_loop") and not any(map(started, background)), names
    else:
        assert all(map(started, background + ("snooze",))), names
        assert names.count("worker_loop") == 2 and realtime.start in spawned, names
    out = capsys.readouterr().out
    assert ("auto-sync disabled" in out) == read_only
    assert "[ai] Claude API" in out
