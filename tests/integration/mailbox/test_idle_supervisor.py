"""IDLE's supervisor (realtime.reconcile): the workers follow the configuration as it changes. An
account added is watched, one removed or disabled stops being watched at once, and one now reached
another way gets a fresh worker; a change that doesn't touch how it is reached leaves the worker
be. The account events it hears come from config (mailmind.accounts.domain.events). Each test has a supervisor of its own,
and sees its workers through the sessions they open."""
import threading

import pytest

from fixtures import world as w
from waiting import wait_until

DEE = "dee@example.test"


def count(world, command, user=w.ADA):
    return len(world.server.commands_for(user, command))


def idling(world, user, n=1):
    return wait_until(lambda: count(world, "IDLE", user) >= n)


def edit_by_hand(email, **fields):
    """Change an account in the config file as an editor would, publishing no event. A test asks this once every account
    idles, so each has pinned its first certificate and no worker writes an older copy of the file over the edit."""
    from mailmind.accounts.adapters import config_file
    from mailmind.bootstrap.accounts import config
    raw = config_file.FILE.read()
    next(a for a in raw["accounts"] if a["email"] == email).update(fields)
    config.save(raw)


def all_idling(world):
    return all(idling(world, user) for user in (w.ADA, w.BEA, w.CY))


@pytest.fixture
def supervisor(world):
    """IDLE's supervisor as the app builds it, of the test's own, hearing the account events, and every worker it starts
    stopped after the test. `opened` holds whose session each worker opened, on which thread, and `interrupted` whose was
    interrupted; `made(acct, session)` is what a worker gets of each session, which a test may change."""
    from mailmind.accounts.domain import events
    from mailmind.bootstrap import accounts, mailbox
    from mailmind.mailbox.application.realtime import Realtime
    from mailmind.platform import bus

    def opening(acct, **kw):
        realtime.opened.append((acct["email"], threading.current_thread()))
        session = mailbox.open_session(acct, **kw)
        interrupt = session.interrupt

        def interrupting():
            realtime.interrupted.append(acct["email"])
            interrupt()
        session.interrupt = interrupting
        return realtime.made(acct, session)
    realtime = Realtime(accounts, opening)
    realtime.opened, realtime.interrupted, realtime.made = [], [], lambda acct, session: session
    ends = [bus.subscribe(t, realtime.reconcile)
            for t in (events.AccountAdded, events.AccountChanged, events.AccountRemoved)]
    yield realtime
    for end in ends:
        end()
    realtime.stop()
    threads = {t for _, t in realtime.opened}
    for t in threads:
        t.join(30)
    assert not any(t.is_alive() for t in threads), "an IDLE worker didn't stop"


def worker_of(supervisor, email):
    """The thread of the newest worker to open a session for `email`, once one has."""
    assert wait_until(lambda: any(who == email for who, _ in supervisor.opened))
    return [t for who, t in supervisor.opened if who == email][-1]


def test_start_watches_every_account(world, supervisor):
    supervisor.start()
    assert all(idling(world, user) for user in (w.ADA, w.BEA, w.CY))
    assert supervisor.watching() == {w.ADA: True, w.BEA: True, w.CY: True}


def test_a_removed_account_stops_being_watched_at_once(world, supervisor):
    from mailmind.bootstrap.accounts import config
    supervisor.start()
    assert idling(world, w.BEA)
    worker = worker_of(supervisor, w.BEA)
    assert config.remove_account(w.BEA)
    worker.join(30)                                     # out of an IDLE it would hold for 24 minutes
    assert not worker.is_alive() and set(supervisor.watching()) == {w.ADA, w.CY}
    assert not any(t.is_alive() for who, t in supervisor.opened if who == w.BEA)     # none is left to talk to her server


def test_a_disabled_account_stops_being_watched(world, supervisor):
    from mailmind.bootstrap.accounts import config
    supervisor.start()
    assert idling(world, w.CY)
    worker = worker_of(supervisor, w.CY)
    config.update_account(w.CY, {"disabled": True})
    worker.join(30)
    assert not worker.is_alive() and w.CY not in supervisor.watching()


def test_a_changed_password_gets_a_fresh_worker_that_logs_in_with_it(world, supervisor):
    from mailmind.bootstrap.accounts import config
    supervisor.start()
    assert idling(world, w.ADA)
    old, logins = worker_of(supervisor, w.ADA), count(world, "LOGIN")
    with world.server.lock:
        world.server.accounts[w.ADA].password = "a-new-app-password"
    config.update_account(w.ADA, {"credential": {"style": "plain", "value": "a-new-app-password"}})
    old.join(30)
    assert not old.is_alive()
    assert idling(world, w.ADA, 2)
    assert count(world, "LOGIN") == logins + 1
    assert worker_of(supervisor, w.ADA) is not old and supervisor.watching()[w.ADA]


def test_a_new_alias_leaves_the_worker_be(world, supervisor):
    from mailmind.bootstrap.accounts import config
    supervisor.start()
    assert idling(world, w.ADA)
    old, logins = worker_of(supervisor, w.ADA), count(world, "LOGIN")
    config.update_account(w.ADA, {"alias": "Ada at home"})          # whose event the supervisor hears before this returns
    assert supervisor.interrupted == [] and worker_of(supervisor, w.ADA) is old and old.is_alive()
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
    supervisor.FIRST_REST = 3600                        # one left to rest is still resting when the test gives up on it
    supervisor.start()
    assert wait_until(lambda: count(world, "LOGIN") >= 1)
    worker = worker_of(supervisor, w.ADA)
    config.remove_account(w.ADA)
    worker.join(30)
    assert not worker.is_alive()


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
    assert wait_until(lambda: [who for who, _ in supervisor.opened].count(w.ADA) >= 12)    # a dozen tries
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
    worker = worker_of(supervisor, w.ADA)
    worker.join(30)
    assert not worker.is_alive() and supervisor.watching()[w.ADA] is False    # the periodic sync has it
    assert count(world, "LOGOUT") == logouts + 1 and count(world, "IDLE") == 0
    assert world.server.commands_for(w.ADA, "SELECT") == []


def test_a_server_without_idle_is_asked_to_log_out_once(world, supervisor):
    """The wire can't show the second ask, which goes to a socket the first closed: the session can."""
    world.server.without = {"IDLE"}
    asked = []

    def counting(acct, session):
        logout = session.logout

        def logging_out():
            asked.append(acct["email"])
            return logout()
        session.logout = logging_out
        return session
    supervisor.made = counting
    supervisor.start()
    worker = worker_of(supervisor, w.ADA)
    worker.join(30)
    assert not worker.is_alive()
    assert asked.count(w.ADA) == 1


def test_a_worker_whose_account_was_changed_by_hand_hands_over_to_a_fresh_one(world, supervisor, monkeypatch):
    monkeypatch.setattr(supervisor, "IDLE_SECONDS", 0.1)
    supervisor.start()
    assert all_idling(world)
    old, logins = worker_of(supervisor, w.ADA), count(world, "LOGIN")
    edit_by_hand(w.ADA, strip_spaces=True)              # no event: as an edit of the file would be
    old.join(30)
    assert not old.is_alive()
    assert wait_until(lambda: count(world, "LOGIN") == logins + 1)
    assert worker_of(supervisor, w.ADA) is not old and supervisor.watching()[w.ADA]


def test_asking_for_another_imap_adapter_hands_over_to_a_fresh_worker(world, supervisor, monkeypatch):
    from mailmind.bootstrap import mailbox
    mailbox.offer("imapclient")                         # front.build's
    monkeypatch.setattr(supervisor, "IDLE_SECONDS", 0.1)
    supervisor.start()
    assert all_idling(world)
    old, logins = worker_of(supervisor, w.ADA), count(world, "LOGIN")
    edit_by_hand(w.ADA, imap_adapter="imapclient")
    old.join(30)
    assert not old.is_alive()
    assert wait_until(lambda: count(world, "LOGIN") == logins + 1)
    assert worker_of(supervisor, w.ADA) is not old and supervisor.watching()[w.ADA]


def test_before_start_and_after_stop_the_supervisor_starts_nothing(world, supervisor):
    from mailmind.bootstrap.accounts import config
    config.update_account(w.ADA, {"strip_spaces": True})
    assert supervisor.watching() == {}
    supervisor.start()
    assert idling(world, w.ADA)
    supervisor.stop()
    assert supervisor.reconcile() == 0 and supervisor.watching() == {}
