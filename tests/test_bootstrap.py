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

