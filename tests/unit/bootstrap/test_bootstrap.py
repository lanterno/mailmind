"""mailmind.bootstrap.wiring: what `python -m mailmind serve` and the app fixture connect before anything
runs, what serving starts, and the background jobs each profile schedules."""
import threading
import time

import pytest

from mailmind.platform.workers.scheduler import Scheduler
from mailmind.compose.api import Sent
from mailmind.mailbox.domain.events import Applied
from mailmind.mailbox.domain.events import InboxChanged
from mailmind.bootstrap import mailbox
from mailmind.bootstrap import background
from mailmind.bootstrap import wiring


@pytest.fixture
def unwired(monkeypatch):
    """No wiring on the bus, and the server's jobs as they were, after the test."""
    monkeypatch.setattr(background, "JOBS", background.JOBS)
    yield
    wiring.unwire()


def subscribers():
    from mailmind.platform.bus import BUS
    return [BUS.subscribers(topic) for topic in (Sent, Applied, InboxChanged)]


def test_the_server_hears_what_the_workers_publish(unwired):
    """That they hold the IMAP lock for their mail commands, test_hwf_http checks at the fake
    server."""
    wiring.wire()
    assert subscribers() == [(wiring._after_send,), (mailbox.filing.settled,), (wiring._after_idle,)]
    wiring.wire()                                       # wiring again replaces, never doubles
    assert subscribers() == [(wiring._after_send,), (mailbox.filing.settled,), (wiring._after_idle,)]
    wiring.unwire()
    assert subscribers() == [(), (), ()]


def test_an_account_added_changed_or_removed_brings_idle_in_line(unwired):
    from mailmind.bootstrap.mailbox import realtime
    from mailmind.accounts.domain import events
    from mailmind.platform.bus import BUS
    topics = (events.AccountAdded, events.AccountChanged, events.AccountRemoved)
    wiring.wire()
    wiring.wire()
    assert [BUS.subscribers(t)[-1:] for t in topics] == [(realtime.reconcile,)] * 3
    wiring.unwire()
    assert [BUS.subscribers(t) for t in topics] == [()] * 3


def test_a_refused_login_is_held_back_until_the_account_changes(unwired):
    from mailmind.bootstrap.accounts import logins
    from mailmind.accounts.domain import events
    from mailmind.platform.bus import BUS
    wiring.wire()
    wiring.wire()
    assert BUS.subscribers(events.CredentialsRejected) == (logins.rejected,)
    assert BUS.subscribers(events.AccountChanged)[0] == BUS.subscribers(events.AccountRemoved)[0] == logins.changed    # ahead of IDLE
    wiring.unwire()
    assert BUS.subscribers(events.CredentialsRejected) == ()


def test_an_idle_change_syncs_those_accounts(unwired, monkeypatch):
    from mailmind.platform import bus
    synced = []
    monkeypatch.setattr(mailbox.syncing, "sync_guarded", synced.append)
    wiring.wire()
    bus.publish(InboxChanged(frozenset({"ada@example.test", "cy@example.test"})))
    assert synced == [{"ada@example.test", "cy@example.test"}]


def test_idles_dispatcher_publishes_one_change_for_a_burst(unwired, monkeypatch):
    from mailmind.bootstrap.mailbox import realtime
    from mailmind.platform import bus
    heard = []
    stop = bus.subscribe(InboxChanged, heard.append)
    monkeypatch.setattr(realtime, "_DEBOUNCE", 0.05)
    monkeypatch.setattr(realtime, "_stop", threading.Event())
    monkeypatch.setattr(realtime, "_kick", threading.Event())
    try:
        for email in ("ada@example.test", "ada@example.test", "cy@example.test"):
            realtime._trigger_sync(email)
        t = threading.Thread(target=realtime._dispatch_loop, daemon=True)
        t.start()
        deadline = time.monotonic() + 5
        while not heard and time.monotonic() < deadline:
            time.sleep(0.01)
        realtime._stop.set()
        realtime._kick.set()
        t.join(5)
    finally:
        stop()
    assert heard == [InboxChanged(frozenset({"ada@example.test", "cy@example.test"}))]



@pytest.mark.parametrize("env", [{"MAILMIND_PROFILE": "read-only"}, {"MAILMIND_NOSYNC": "1"},
                                 {"MAILMIND_NOSYNC": "true"}, {}],
                         ids=["read-only", "nosync", "nosync-true", "full"])
def test_starting_makes_the_schema_wires_the_workers_and_starts_them(env, tmp_path, unwired, monkeypatch,
                                                                     capsys):
    """What runs before the app serves. Read-only, only snooze wake starts, since it
    needs no mail server: the outbox and the offline queue wait, and nothing syncs."""
    from mailmind.bootstrap.mailbox import realtime
    from mailmind.bootstrap.mailbox import store
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
        wiring.start(spawn=spawned.append)
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
        assert names[:-1] == ["worker_loop", "worker_loop", "job:snooze", "job:sync", "job:draft-push", "job:assess",
                              "job:backfill", "job:draft-ahead", "job:reconcile", "job:expire",
                              "job:forget"]
        assert spawned[-1] is realtime.start
    assert [j["name"] for j in background.JOBS.snapshot()] == [n.removeprefix("job:") for n in names if "job:" in n]
    out = capsys.readouterr().out
    assert ("auto-sync disabled" in out) == read_only
    assert "[ai] Claude API" in out
    assert "[imap] mail through imaplib" in out


def test_an_account_gets_imapclient_only_where_it_is_offered(world):
    from mailmind.bootstrap import accounts
    from fixtures import world as w
    from mailmind.bootstrap import mailbox
    w.ask_for_imapclient(world.dir, w.CY)

    def adapters():
        sessions = {who: mailbox.open_session(accounts.get(who), timeout=10) for who in (w.ADA, w.CY)}
        for s in sessions.values():
            s.logout()
        return {who: type(s).__name__ for who, s in sessions.items()}
    assert adapters() == {w.ADA: "ImaplibSession", w.CY: "ImaplibSession"}       # the command line's
    assert mailbox.describe() == "mail through imaplib"
    mailbox.offer("imapclient")                                                  # front.build's
    assert adapters() == {w.ADA: "ImaplibSession", w.CY: "ImapClientSession"}
    assert mailbox.describe() == "mail through imaplib, imapclient for 1 account that asked"
    mailbox.use("imapclient")                                                    # every account's
    assert adapters() == {w.ADA: "ImapClientSession", w.CY: "ImapClientSession"}


def test_the_app_runs_the_account_that_asks_on_imapclient_and_every_other_on_imaplib(app, world):
    from mailmind.bootstrap import accounts
    from fixtures import world as w
    from mailmind.bootstrap import mailbox
    assert (mailbox.kind_for(accounts.get(w.CY)), mailbox.kind_for(accounts.get(w.ADA))) == ("imaplib", "imaplib")
    w.ask_for_imapclient(world.dir, w.CY)
    assert (mailbox.kind_for(accounts.get(w.CY)), mailbox.kind_for(accounts.get(w.ADA))) == ("imapclient", "imaplib")


def test_the_app_reads_an_invite_through_the_library(app):
    from mailmind.bootstrap import calendar
    assert calendar.kind() == "icalendar"


def scheduled(monkeypatch, **env):
    """The jobs background.schedule puts on a fresh scheduler under `env`: name, interval and first delay."""
    for name in ("MAILMIND_PROFILE", "MAILMIND_NOSYNC", "MAILMIND_NO_DRAFT_TICK"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    jobs = Scheduler()
    background.schedule(jobs)
    return [(j.name, j.every, j.first) for j in jobs._jobs.values()]


def test_the_full_profile_schedules_each_job_at_its_pace(monkeypatch):
    assert scheduled(monkeypatch) == [("snooze", 60, 60), ("sync", 300, 0), ("draft-push", 10, 5), ("assess", 300, 20),
                                      ("backfill", 600, 45), ("draft-ahead", 180, 120),
                                      ("reconcile", 1800, 120), ("expire", 3600, 180),
                                      ("forget", 86400, 600)]


def test_read_only_schedules_snooze_wake_alone(monkeypatch):
    assert scheduled(monkeypatch, MAILMIND_PROFILE="read-only") == [("snooze", 60, 60)]


def test_a_job_switched_off_is_left_out(monkeypatch):
    monkeypatch.setattr(mailbox.reconciler, "_RECONCILE_INTERVAL", 0)
    monkeypatch.setattr(background, "_EXPIRE_INTERVAL", 0)
    assert [n for n, *_ in scheduled(monkeypatch, MAILMIND_NO_DRAFT_TICK="1")] == ["snooze", "sync", "draft-push", "assess",
                                                                                      "backfill", "forget"]


def test_a_short_reconcile_cycle_still_waits_30_seconds_between_rounds(monkeypatch):
    monkeypatch.setattr(mailbox.reconciler, "_RECONCILE_INTERVAL", 10)
    assert ("reconcile", 30, 10) in scheduled(monkeypatch)


def test_the_sync_jobs_first_round_resolves_the_credentials_and_no_other_does(monkeypatch):
    from mailmind.bootstrap import accounts
    calls = []
    monkeypatch.setattr(accounts, "preload_creds", lambda: calls.append("credentials") or 0)
    monkeypatch.setattr(mailbox.syncing, "sync_once", lambda: calls.append("sync"))
    sync_round = mailbox.syncing.sync_rounds()
    sync_round()
    sync_round()
    assert calls == ["credentials", "sync", "sync"]
