"""mailmind.accounts.domain.health: what kind of trouble an error names, from its class alone, and how an account stands."""
import socket
import ssl

import pytest

from mailmind.accounts.adapters.imap_connection import LoginUnreachable
from mailmind.accounts.domain import health, tls_pin


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
