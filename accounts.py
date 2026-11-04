"""IMAP connect layer for MailMind. The account list + credentials now live in
`mailmind.config.json` (see config.py / mailmind.config.example.json). This
module just turns a config account into a logged-in IMAP connection and keeps
the small API (accounts/get/connect/trash_folder/preload_creds) that the rest
of the app imports.
"""
import imaplib
import ssl

import config
from mailmind.accounts.domain import provider, tls_pin
from mailmind.mailbox.adapters.imap_names import BadMailbox, imap_quote, mailbox_arg   # noqa: F401 (callers' names)
from mailmind.platform import profile
from mailmind.platform.db import txguard


def accounts():
    return config.accounts()


def get(email):
    for a in accounts():
        if a["email"] == email:
            return a
    raise KeyError(email)


def trash_folder(acct):
    return acct.get("trash") or provider.resolve(acct).trash_fallback


def preload_creds():
    return config.preload()


class LoginUnreachable(OSError):
    """A connect-time login failure (expired/revoked credentials, Bridge restart). Subclasses
    OSError so it's a member of the offline-queue's _UNREACHABLE set — a mutation during an auth
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


def check_pin(acct, host, port, conn):
    """On loopback, where tls_context verifies nothing, pin the certificate the account meets first
    at this endpoint, and refuse any other after it (mailmind.accounts.domain.tls_pin). `conn` is
    the IMAP or SMTP connection, its TLS socket `conn.sock`. Raises TLSPinMismatch, a
    ConnectionError, before a credential is sent. Elsewhere it does nothing."""
    if not is_loopback(host):
        return
    seen = tls_pin.fingerprint(conn.sock.getpeercert(binary_form=True))
    where = tls_pin.endpoint(host, port)
    pinned = config.tls_pin(acct["email"], where, seen)
    if pinned is not None and pinned != seen:
        raise tls_pin.TLSPinMismatch(f"the certificate at {where} isn't the one pinned for it")


def connect(acct, timeout=60):
    txguard.refuse_network("an IMAP connection")
    if acct["starttls"]:  # e.g. Proton Bridge: plain socket + STARTTLS
        M = imaplib.IMAP4(acct["host"], acct["port"], timeout=timeout)
        txguard.guard_calls(M, "_command", lambda name, *args: f"IMAP {name}")   # every command
        M.starttls(tls_context(acct["host"]))
    else:  # direct TLS (Gmail + most hosted IMAP on 993; Bridge set to SSL)
        M = imaplib.IMAP4_SSL(acct["host"], acct["port"], ssl_context=tls_context(acct["host"]), timeout=timeout)
        txguard.guard_calls(M, "_command", lambda name, *args: f"IMAP {name}")
    try:
        check_pin(acct, acct["host"], acct["port"], M)
    except tls_pin.TLSPinMismatch:
        M.shutdown()
        raise
    try:
        M.login(acct["email"], config.resolve_credential(acct))
    except imaplib.IMAP4.error as e:   # login NO/BAD = a connection-level fault, not a per-command one
        raise LoginUnreachable(str(e)) from e
    return M if profile.writes_allowed() else ReadOnlyIMAP(M)


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
