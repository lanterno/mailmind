"""A loopback server's certificate, pinned on first use.

Off this machine every certificate is verified. On loopback it can't be: Proton Bridge serves a
certificate it signed itself. So the first certificate an account meets at a loopback endpoint is
pinned, by the SHA-256 of its DER bytes, and from then on a connection there that presents any
other is closed before a credential is sent. Something else listening on Bridge's port while
Bridge is down then gets nothing. When the certificate changes for a good reason (Bridge
reinstalled or reset), the account's pins are forgotten and the next connection pins afresh.
"""
import hashlib

LOOPBACK = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}


def is_loopback(host: str | None) -> bool:
    """On this machine, the only place a certificate's checks may be skipped: nowhere else does a pin stand in for them."""
    return (host or "").strip().lower() in LOOPBACK


class TLSPinMismatch(ConnectionError):
    """The server presented a certificate other than the one pinned for it. Nothing was sent."""


def fingerprint(der: bytes | None) -> str:
    return "sha256:" + hashlib.sha256(der or b"").hexdigest()


def endpoint(host: str | None, port: int | str) -> str:
    """The key a pin is kept under: the host as configured, and the port."""
    return f"{(host or '').strip().lower()}:{int(port)}"
