"""IDLE's supervisor (realtime.reconcile): the workers follow the configuration as it changes. An
account added is watched, one removed or disabled stops being watched at once, and one now reached
another way gets a fresh worker; a change that doesn't touch how it is reached leaves the worker
be. The account events it hears come from config (mailmind.accounts.domain.events)."""
import threading
import time

import pytest

from fixtures import world as w

DEE = "dee@example.test"


def wait_until(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def count(world, command, user=w.ADA):
    return len(world.server.commands_for(user, command))


def idling(world, user, n=1):
    return wait_until(lambda: count(world, "IDLE", user) >= n)


def edit_by_hand(email, **fields):
    """Change an account in the config file as an editor would, publishing no event. It holds the
    config's write lock meanwhile, as the app's own writers do, so a worker pinning its first
    certificate can't write its older copy of the file over the edit."""
    from mailmind.bootstrap.accounts import config
    with config._write_lock:
        raw = config._raw()
        next(a for a in raw["accounts"] if a["email"] == email).update(fields)
        config.save(raw)


@pytest.fixture
def supervisor(world, monkeypatch):
    """realtime with state of its own, hearing the account events, and every worker it starts
    stopped after the test."""
    from mailmind.bootstrap.mailbox import realtime
    from mailmind.accounts.domain import events
    from mailmind.platform import bus
    for name, value in (("_stop", threading.Event()), ("_kick", threading.Event()), ("_dirty", set()),
                        ("_conns", []), ("_watchers", {}), ("_running", False)):
        monkeypatch.setattr(realtime, name, value)
    threads = []
    spawn = realtime._spawn

    def spawned(acct):
        watch = spawn(acct)
        threads.append(watch.thread)
        return watch
    monkeypatch.setattr(realtime, "_spawn", spawned)
    ends = [bus.subscribe(t, realtime.reconcile)
            for t in (events.AccountAdded, events.AccountChanged, events.AccountRemoved)]
    yield realtime
    for end in ends:
        end()
    realtime.stop()
    for t in threads:
        t.join(5)
    assert not any(t.is_alive() for t in threads), "an IDLE worker didn't stop"


def test_start_watches_every_account(world, supervisor):
    supervisor.start()
    assert all(idling(world, user) for user in (w.ADA, w.BEA, w.CY))
    assert supervisor.watching() == {w.ADA: True, w.BEA: True, w.CY: True}


def test_a_removed_account_stops_being_watched_at_once(world, supervisor):
    from mailmind.bootstrap.accounts import config
    supervisor.start()
    assert idling(world, w.BEA)
    worker = supervisor._watchers[w.BEA].thread
    assert config.remove_account(w.BEA)
    worker.join(5)                                      # out of an IDLE it would hold for 24 minutes
    assert not worker.is_alive() and set(supervisor.watching()) == {w.ADA, w.CY}
    n = len(world.server.commands_for(w.BEA))
    time.sleep(0.2)
    assert len(world.server.commands_for(w.BEA)) == n


def test_a_disabled_account_stops_being_watched(world, supervisor):
    from mailmind.bootstrap.accounts import config
    supervisor.start()
    assert idling(world, w.CY)
    worker = supervisor._watchers[w.CY].thread
    config.update_account(w.CY, {"disabled": True})
    worker.join(5)
    assert not worker.is_alive() and w.CY not in supervisor.watching()


def test_a_changed_password_gets_a_fresh_worker_that_logs_in_with_it(world, supervisor):
    from mailmind.bootstrap.accounts import config
    supervisor.start()
    assert idling(world, w.ADA)
    old, logins = supervisor._watchers[w.ADA], count(world, "LOGIN")
    with world.server.lock:
        world.server.accounts[w.ADA].password = "a-new-app-password"
    config.update_account(w.ADA, {"credential": {"style": "plain", "value": "a-new-app-password"}})
    old.thread.join(5)
    assert not old.thread.is_alive()
    assert idling(world, w.ADA, 2)
    assert count(world, "LOGIN") == logins + 1
    assert supervisor._watchers[w.ADA] is not old and supervisor.watching()[w.ADA]


def test_a_new_alias_leaves_the_worker_be(world, supervisor):
    from mailmind.bootstrap.accounts import config
    supervisor.start()
    assert idling(world, w.ADA)
    old, logins = supervisor._watchers[w.ADA], count(world, "LOGIN")
    config.update_account(w.ADA, {"alias": "Ada at home"})
    assert supervisor._watchers[w.ADA] is old and old.thread.is_alive()
    assert count(world, "LOGIN") == logins


def test_an_added_account_is_watched(world, supervisor):
    from mailmind.bootstrap.accounts import config
    supervisor.start()
    entry = world.server.add_account(DEE)
    config.add_account({"email": DEE, "type": "imap", "host": entry["host"], "port": entry["port"],
                        "starttls": entry["starttls"], "credential": {"style": "plain", "value": "app-password"}})
    assert idling(world, DEE)
    assert supervisor.watching()[DEE]


def test_past_the_cap_a_removed_account_makes_room_for_the_next(world, supervisor, monkeypatch):
    from mailmind.bootstrap.accounts import config
    monkeypatch.setattr(supervisor, "MAX_IDLE_ACCOUNTS", 2)
    supervisor.start()
    assert set(supervisor.watching()) == {w.ADA, w.BEA}
    config.remove_account(w.ADA)
    assert idling(world, w.CY)
    assert set(supervisor.watching()) == {w.BEA, w.CY}


def test_a_worker_backing_off_stops_at_once_when_its_account_goes(world, supervisor):
    from mailmind.bootstrap.accounts import config
    world.server.drop("LOGIN", user=w.ADA, times=None)
    supervisor.start()
    assert wait_until(lambda: count(world, "LOGIN") >= 1)
    worker = supervisor._watchers[w.ADA].thread
    began = time.monotonic()
    config.remove_account(w.ADA)
    worker.join(5)
    assert not worker.is_alive() and time.monotonic() - began < 2     # its backoff is 5 seconds


@pytest.fixture
def refused_logins_held(monkeypatch, supervisor):
    """As wiring.wire does, with a worker that rests for moments rather than seconds, so it has every chance to try again."""
    from mailmind.accounts.domain import events
    from mailmind.bootstrap import accounts
    from mailmind.platform import bus
    monkeypatch.setattr(supervisor, "FIRST_REST", 0.01)
    monkeypatch.setattr(supervisor, "LONGEST_REST", 0.02)
    ends = [bus.subscribe(events.CredentialsRejected, accounts.logins.rejected),
            bus.subscribe(events.AccountChanged, accounts.logins.changed)]
    yield
    for end in ends:
        end()
    accounts.logins.forget(w.ADA)


REFUSED = "NO [AUTHENTICATIONFAILED] invalid credentials"


def test_a_refused_login_stops_a_worker_sending_it(world, supervisor, refused_logins_held):
    world.server.fail("LOGIN", REFUSED, user=w.ADA, times=None)
    supervisor.start()
    assert wait_until(lambda: count(world, "LOGIN") == 1)
    assert idling(world, w.BEA) and idling(world, w.CY)         # the others are no worse off
    time.sleep(0.3)                                             # a dozen rests or more
    assert count(world, "LOGIN") == 1
    assert w.ADA in supervisor.watching()                       # it is still there for when the hold ends


def test_a_worker_for_a_refused_login_starts_afresh_on_a_new_password(world, supervisor, refused_logins_held):
    from mailmind.bootstrap.accounts import config
    world.server.accounts[w.ADA].password = "the-new-password"
    supervisor.start()
    assert wait_until(lambda: count(world, "LOGIN") == 1)
    config.update_account(w.ADA, {"credential": {"style": "plain", "value": "the-new-password"}})
    assert idling(world, w.ADA)
    assert count(world, "LOGIN") == 2


def test_a_server_without_idle_ends_its_worker_and_is_logged_out_of(world, supervisor):
    world.server.without = {"IDLE"}
    logouts = count(world, "LOGOUT")
    supervisor.start()
    worker = supervisor._watchers[w.ADA].thread
    worker.join(5)
    assert not worker.is_alive() and supervisor.watching()[w.ADA] is False    # the periodic sync has it
    assert count(world, "LOGOUT") == logouts + 1 and count(world, "IDLE") == 0
    assert world.server.commands_for(w.ADA, "SELECT") == [] and supervisor._conns == []


def test_a_server_without_idle_is_asked_to_log_out_once(world, supervisor, monkeypatch):
    """The wire can't show the second ask, which goes to a socket the first closed: the session can."""
    world.server.without = {"IDLE"}
    asked = []
    opening = supervisor._open_session

    def counting(acct):
        session = opening(acct)
        logout = session.logout

        def logging_out():
            asked.append(acct["email"])
            return logout()
        session.logout = logging_out
        return session
    monkeypatch.setattr(supervisor, "_open_session", counting)
    supervisor.start()
    worker = supervisor._watchers[w.ADA].thread
    worker.join(5)
    assert not worker.is_alive()
    assert asked.count(w.ADA) == 1


def test_a_worker_whose_account_was_changed_by_hand_hands_over_to_a_fresh_one(world, supervisor, monkeypatch):
    monkeypatch.setattr(supervisor, "IDLE_SECONDS", 0.1)
    supervisor.start()
    assert idling(world, w.ADA)
    old, logins = supervisor._watchers[w.ADA], count(world, "LOGIN")
    edit_by_hand(w.ADA, strip_spaces=True)              # no event: as an edit of the file would be
    old.thread.join(5)
    assert not old.thread.is_alive()
    assert wait_until(lambda: count(world, "LOGIN") == logins + 1)
    assert supervisor._watchers[w.ADA] is not old and supervisor.watching()[w.ADA]


def test_asking_for_another_imap_adapter_hands_over_to_a_fresh_worker(world, supervisor, monkeypatch):
    from mailmind.bootstrap import mailbox
    mailbox.offer("imapclient")                         # front.build's
    monkeypatch.setattr(supervisor, "IDLE_SECONDS", 0.1)
    supervisor.start()
    assert idling(world, w.ADA)
    old, logins = supervisor._watchers[w.ADA], count(world, "LOGIN")
    edit_by_hand(w.ADA, imap_adapter="imapclient")
    old.thread.join(5)
    assert not old.thread.is_alive()
    assert wait_until(lambda: count(world, "LOGIN") == logins + 1)
    assert supervisor._watchers[w.ADA] is not old and supervisor.watching()[w.ADA]


def test_the_configuration_says_what_happened_to_each_account(data_dir):
    from mailmind.bootstrap.accounts import config
    from mailmind.accounts.domain import events
    from mailmind.platform import bus
    heard = []
    ends = [bus.subscribe(t, heard.append) for t in (events.AccountAdded, events.AccountChanged, events.AccountRemoved)]
    try:
        config.add_account({"email": DEE, "type": "imap", "host": "mail.example.test",
                            "credential": {"style": "plain", "value": "app-password"}})
        config.update_account(DEE, {"alias": "Dee"})
        assert config.remove_account(DEE)
        with pytest.raises(ValueError):
            config.add_account({"email": "no-at-sign", "credential": {"style": "plain", "value": "x"}})
        assert config.update_account(DEE, {"alias": "Dee"}) is None
        assert config.remove_account(DEE) is False
    finally:
        for end in ends:
            end()
    assert heard == [events.AccountAdded(DEE), events.AccountChanged(DEE), events.AccountRemoved(DEE)]


def test_before_start_and_after_stop_the_supervisor_starts_nothing(world, supervisor):
    from mailmind.bootstrap.accounts import config
    config.update_account(w.ADA, {"strip_spaces": True})
    assert supervisor.watching() == {}
    supervisor.start()
    assert idling(world, w.ADA)
    supervisor.stop()
    assert supervisor.reconcile() == 0 and supervisor.watching() == {}
