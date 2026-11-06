"""Which MailSession adapter (mailmind.mailbox.application.session) the mail goes through, and how
a session opens.

Every account's mail goes through imaplib (mailbox.adapters.imaplib_session) over accounts.connect,
unless it asks for IMAPClient ("imap_adapter": "imapclient" in its config) and the process offers
it. Only the new door's build offers it, loading it then, so the old door never imports it and
an account's ask changes nothing there. That ask is how IMAPClient soaks on real accounts before
it becomes the default. use(kind) makes `kind` every account's, as the contract tests do.

Either way a session opens the same way: no transaction open across it, the account's TLS
context, the loopback certificate pin before LOGIN, a refused login as accounts.LoginUnreachable,
every command checked by txguard, and in a read-only process a ReadOnlySession.
"""
from collections import Counter
import imaplib

from mailmind.accounts.domain import tls_pin
from mailmind.discovery.adapters import fts
from mailmind.mailbox.adapters import sqlite as store
from mailmind.mailbox.adapters.imap_names import imap_quote
from mailmind.mailbox.adapters.imaplib_session import ImaplibSession
from mailmind.accounts.domain import provider
from mailmind.mailbox.application.commands import MailCommands
from mailmind.mailbox.application.copies import Copies
from mailmind.mailbox.application.offline_queue import OfflineQueue
from mailmind.mailbox.application.pool import Pool
from mailmind.mailbox.application.session import ReadOnlySession
from mailmind.platform import profile
from mailmind.platform.db import txguard

from mailmind.bootstrap import accounts         # the connection's TLS, pin and login, until they move into Accounts
from mailmind.bootstrap.accounts import config           # the credential, likewise

store.bind_index(fts)

KINDS = ("imaplib", "imapclient")
_kind = "imaplib"               # every account's, but one asking for another this process offers
_offered = {"imaplib"}


def offer(kind):
    """Let an account that asks for `kind` have it."""
    if kind not in KINDS:
        raise ValueError(f"no mail session adapter {kind!r}")
    if kind == "imapclient":
        from mailmind.mailbox.adapters import imapclient_session  # noqa: F401  (missing: fail now, not mid-sync)
    _offered.add(kind)


def use(kind):
    """Open every account's sessions through `kind` from now on, but one asking for another."""
    global _kind
    offer(kind)
    _kind = kind


def reset():
    """imaplib for every account, and nothing else offered: how a process starts."""
    global _kind
    _kind = "imaplib"
    _offered.intersection_update({"imaplib"})


def kind():
    return _kind


def kind_for(acct):
    """The adapter the account `acct` (a config.accounts() entry) goes through."""
    asked = acct.get("imap_adapter")
    return asked if asked in _offered else _kind


def describe():
    """The adapters in use, for the startup line: each with how many accounts it serves, the
    one every account gets first. A configuration that can't be read counts no account."""
    try:
        counts = Counter(kind_for(a) for a in config.accounts())
    except Exception:
        counts = Counter()
    rest = sorted(k for k in counts if k != _kind)
    return ", ".join([f"mail through {_kind}"] + [
        f"{k} for {counts[k]} account{'' if counts[k] == 1 else 's'} that asked" for k in rest])


def open_session(acct, timeout=60):
    """A logged-in MailSession for the account `acct` (a config.accounts() entry)."""
    if kind_for(acct) == "imaplib":
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


def _open(acct, **kw):
    return open_session(acct, **kw)


class _Accounts:
    """What the Mailbox asks of Accounts."""

    def get(self, email):
        return accounts.get(email)

    def accounts(self):
        return accounts.accounts()

    def trash_folder(self, acct):
        return accounts.trash_folder(acct)

    def profile(self, acct):
        return provider.resolve(acct)


_directory = _Accounts()
copies = Copies(_open, imap_quote)
commands = MailCommands(store, _directory, _open, imap_quote)
offline = OfflineQueue(store, _directory, commands)
pool = Pool(_directory, _open)
