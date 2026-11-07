"""mailmind.accounts.domain.health: what kind of trouble an error names, from its class alone, and how an account stands."""
import socket
import ssl

import pytest

from fixtures import world as w
from mailmind.accounts.adapters.imap_connection import LoginUnreachable
from mailmind.accounts.domain import health, tls_pin


def store_write():
    from mailmind.bootstrap import mailbox
    return mailbox._uow.write()


class UidvalidityChanged(Exception):
    pass


class ImapTrouble(Exception):
    pass


@pytest.mark.parametrize("error, kind", [
    (TimeoutError(), "TIMEOUT"),
    (socket.timeout(), "TIMEOUT"),
    (LoginUnreachable("no"), "AUTH"),
    (UidvalidityChanged(), "UIDVALIDITY"),
    (ConnectionRefusedError(), "NETWORK"),
    (ConnectionResetError(), "NETWORK"),
    (socket.gaierror(), "NETWORK"),
    (ssl.SSLError(), "NETWORK"),
    (tls_pin.TLSPinMismatch(), "TLS_PIN"),
    (ImapTrouble(), "IMAP"),
    (ValueError(), "UNKNOWN"),
], ids=lambda v: v if isinstance(v, str) else type(v).__name__)
def test_an_error_kind_comes_from_the_class(error, kind):
    assert health.error_kind(error) == kind


def test_what_an_error_says_counts_for_nothing():
    """It can hold a password or a host, and it can't move an error to another kind either."""
    assert health.error_kind(ValueError("LOGIN timed out: AUTHENTICATIONFAILED")) == "UNKNOWN"


SYNCED = {"account": "ada@example.test", "last_ok_ts": "2026-11-02T12:00:00Z", "last_error_ts": None,
          "last_error_kind": None, "last_error": None, "sync_count": 3, "live": "ok"}


def test_an_account_is_healthy_once_a_sync_worked_and_none_failed_since():
    assert health.AccountHealth.of(SYNCED).healthy
    assert not health.AccountHealth.of({**SYNCED, "last_ok_ts": None}).healthy
    assert not health.AccountHealth.of({**SYNCED, "last_error": "TimeoutError", "last_error_kind": "TIMEOUT"}).healthy


def test_what_the_page_reads_is_the_record_and_the_verdict():
    assert health.AccountHealth.of(SYNCED).as_row() == {**SYNCED, "healthy": True}
    assert list(health.AccountHealth.of(SYNCED).as_row()) == ["account", "last_ok_ts", "last_error_ts", "last_error_kind",
                                                              "last_error", "sync_count", "healthy", "live"]


def test_health_reads_only_the_inbox_row_and_what_a_sync_is_doing_now(app, world):
    from mailmind.bootstrap import accounts, mailbox
    mailbox.syncing.sync_accounts({w.ADA})
    with store_write() as c:
        c.execute("INSERT OR REPLACE INTO sync_state(account, folder, last_ok_ts, sync_count) VALUES(?, 'Archive', 'then', 9)", (w.ADA,))
    mine = [a for a in accounts.health.answer()["accounts"] if a["account"] == w.ADA]
    assert [(a["sync_count"], a["live"], a["healthy"]) for a in mine] == [(1, "ok", True)]


def test_an_error_shows_by_class_and_kind_and_says_nothing_else(app, world):
    from mailmind.bootstrap import accounts, mailbox
    world.server.fail("LOGIN", "NO [AUTHENTICATIONFAILED] hunter2", user=w.ADA, times=None)
    mailbox.syncing.sync_accounts({w.ADA})
    mine = next(a for a in accounts.health.answer()["accounts"] if a["account"] == w.ADA)
    assert (mine["last_error"], mine["last_error_kind"], mine["live"], mine["healthy"]) == ("LoginUnreachable", "AUTH", "error", False)
    assert "hunter2" not in str(mine)
