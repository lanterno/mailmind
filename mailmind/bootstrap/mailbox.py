"""Which MailSession adapter (mailmind.mailbox.application.session) the mail goes through, and how
a session opens.

The old door and the scripts use imaplib (mailbox.adapters.imaplib_session) over
accounts.connect. use("imapclient") picks IMAPClient instead, loaded only then, so the old door
never imports it. Either way a session opens the same way: no transaction open across it, the
account's TLS context, the loopback certificate pin before LOGIN, a refused login as
accounts.LoginUnreachable, every command checked by txguard, and in a read-only process a
ReadOnlySession.
"""
import imaplib

from mailmind.accounts.domain import tls_pin
from mailmind.mailbox.adapters.imaplib_session import ImaplibSession
from mailmind.mailbox.application.session import ReadOnlySession
from mailmind.platform import profile
from mailmind.platform.db import txguard

import accounts         # the connection's TLS, pin and login, until they move into Accounts
import config           # the credential, likewise

KINDS = ("imaplib", "imapclient")
_kind = "imaplib"


def use(kind):
    """Open sessions through `kind` from now on."""
    global _kind
    if kind not in KINDS:
        raise ValueError(f"no mail session adapter {kind!r}")
    if kind == "imapclient":
        from mailmind.mailbox.adapters import imapclient_session  # noqa: F401  (missing: fail now, not mid-sync)
    _kind = kind


def kind():
    return _kind


def open_session(acct, timeout=60):
    """A logged-in MailSession for the account `acct` (a config.accounts() entry)."""
    if _kind == "imaplib":
        return ImaplibSession(accounts.connect(acct, timeout=timeout))
    from mailmind.mailbox.adapters import imapclient_session
    txguard.refuse_network("an IMAP connection")
    host, port = acct["host"], acct["port"]
    try:
        session = imapclient_session.connect(
            host, port, starttls=bool(acct["starttls"]), ssl_context=accounts.tls_context(host), timeout=timeout,
            check_tls=lambda imap: accounts.check_pin(acct, host, port, imap),
            username=acct["email"], password=lambda: config.resolve_credential(acct),
            guard=lambda imap: txguard.guard_calls(imap, "send", "IMAP command"))
    except (tls_pin.TLSPinMismatch, imaplib.IMAP4.abort):
        raise
    except imaplib.IMAP4.error as e:     # a login NO/BAD is the connection's fault, not a command's
        raise accounts.LoginUnreachable(str(e)) from e
    return session if profile.writes_allowed() else ReadOnlySession(session)
