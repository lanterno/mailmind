"""A config account as a logged-in imaplib connection, with the TLS and the certificate pin every
connection to an account's servers takes, IMAP and SMTP alike."""
import imaplib
import re
import ssl

from mailmind.accounts.domain import tls_pin
from mailmind.platform import profile
from mailmind.platform.db import txguard


class LoginUnreachable(OSError):
    """A connect-time login failure (expired/revoked credentials, Bridge restart). Subclasses
    OSError so it's a member of commands.UNREACHABLE — a mutation during an auth
    outage is QUEUED + retried rather than silently applied locally only."""


LOOPBACK = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}


def is_loopback(host):
    return (host or "").strip().lower() in LOOPBACK


def tls_context(host):
    """Verified TLS, except on loopback, where Proton Bridge serves a self-signed certificate.
    Any host off this machine is verified, so a forged certificate can't capture the password."""
    ctx = ssl.create_default_context()
    if is_loopback(host):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE      # check_pin stands in for the verification
    return ctx


_CAPABILITY_CODE = re.compile(rb"\[CAPABILITY ([^\]]*)\]", re.IGNORECASE)


def _learn_capabilities(M, said):
    """Make M.capabilities the list a logged-in client has. imaplib keeps the one from before
    login, which most servers (Gmail, Dovecot) keep short: MOVE, UIDPLUS and SPECIAL-USE come only
    after it. The login answer usually carries the new list; when it doesn't, ask for it."""
    found = next((m for m in (_CAPABILITY_CODE.search(d) for d in said or () if isinstance(d, bytes)) if m), None)
    if found:
        text = found.group(1)
    else:
        typ, dat = M.capability()
        if typ != "OK" or not dat or not isinstance(dat[-1], bytes):
            return
        text = dat[-1]
    M.capabilities = tuple(text.decode("ascii", "replace").upper().split())


class ReadOnlyIMAP:
    """A read-only process's IMAP connection. Every mailbox opens with EXAMINE, so the server
    changes no flag of its own accord either, and any command not known to only read raises
    profile.ReadOnlyError before it is sent. Everything else is the connection's own."""

    _UID_READS = frozenset({"FETCH", "SEARCH", "SORT", "THREAD"})
    _READS = frozenset({"capability", "check", "close", "enable", "examine", "fetch", "getacl",
                        "getannotation", "getquota", "getquotaroot", "idle", "list", "logout", "lsub",
                        "myrights", "namespace", "noop", "response", "search", "select", "shutdown",
                        "socket", "sort", "status", "thread", "uid", "unselect"})

    def __init__(self, M):
        self._M = M

    def select(self, mailbox="INBOX", readonly=False):
        return self._M.select(mailbox, readonly=True)

    def uid(self, command, *args):
        if str(command).upper() not in self._UID_READS:
            raise profile.ReadOnlyError(f"IMAP UID {str(command).upper()}")
        return self._M.uid(command, *args)

    def __getattr__(self, name):
        value = getattr(self._M, name)
        if callable(value) and name not in self._READS:
            raise profile.ReadOnlyError(f"IMAP {name}")
        return value


class Connector:
    def __init__(self, directory):
        self._directory = directory

    def check_pin(self, acct, host, port, conn):
        """On loopback, where tls_context verifies nothing, pin the certificate the account meets first
        at this endpoint, and refuse any other after it (mailmind.accounts.domain.tls_pin). `conn` is
        the IMAP or SMTP connection, its TLS socket `conn.sock`. Raises TLSPinMismatch, a
        ConnectionError, before a credential is sent. Elsewhere it does nothing."""
        if not is_loopback(host):
            return
        seen = tls_pin.fingerprint(conn.sock.getpeercert(binary_form=True))
        where = tls_pin.endpoint(host, port)
        pinned = self._directory.tls_pin(acct["email"], where, seen)
        if pinned is not None and pinned != seen:
            raise tls_pin.TLSPinMismatch(f"the certificate at {where} isn't the one pinned for it")

    def connect(self, acct, timeout=60):
        txguard.refuse_network("an IMAP connection")
        if acct["starttls"]:  # e.g. Proton Bridge: plain socket + STARTTLS
            M = imaplib.IMAP4(acct["host"], acct["port"], timeout=timeout)
            txguard.guard_calls(M, "_command", lambda name, *args: f"IMAP {name}")   # every command
            M.starttls(tls_context(acct["host"]))
        else:  # direct TLS (Gmail + most hosted IMAP on 993; Bridge set to SSL)
            M = imaplib.IMAP4_SSL(acct["host"], acct["port"], ssl_context=tls_context(acct["host"]), timeout=timeout)
            txguard.guard_calls(M, "_command", lambda name, *args: f"IMAP {name}")
        try:
            self.check_pin(acct, acct["host"], acct["port"], M)
        except tls_pin.TLSPinMismatch:
            M.shutdown()
            raise
        try:
            _, said = M.login(acct["email"], self._directory.resolve_credential(acct))
        except imaplib.IMAP4.error as e:   # login NO/BAD = a connection-level fault, not a per-command one
            raise LoginUnreachable(str(e)) from e
        _learn_capabilities(M, said)
        return M if profile.writes_allowed() else ReadOnlyIMAP(M)
