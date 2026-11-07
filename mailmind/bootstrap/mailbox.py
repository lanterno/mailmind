"""Which MailSession adapter (mailmind.mailbox.application.session) the mail goes through, and how
a session opens.

Every account's mail goes through imaplib (mailbox.adapters.imap.imaplib_session) over accounts.connect,
unless it asks for IMAPClient ("imap_adapter": "imapclient" in its config) and the process offers
it. Only the new door's build offers it, loading it then, so the old door never imports it and
an account's ask changes nothing there. That ask is how IMAPClient soaks on real accounts before
it becomes the default. use(kind) makes `kind` every account's, as the contract tests do.

Either way a session opens the same way: no transaction open across it, the account's TLS
context, the loopback certificate pin before LOGIN, a refused login as accounts.LoginUnreachable and
CredentialsRejected on the bus, every command checked by txguard, and in a read-only process a
ReadOnlySession.
"""
from collections import Counter
import imaplib

from mailmind.accounts.api import CredentialsRejected, TLSPinMismatch, login_id, provider_of
from mailmind.assistant.adapters import sqlite as assistant_store
from mailmind.attention.adapters import sqlite as attention_store
from mailmind.compose.adapters import sqlite as compose_store
from mailmind.discovery.adapters import fts
from mailmind.mailbox.adapters import sqlite as store
from mailmind.mailbox.adapters.imap.imap_names import BadValue, imap_quote
from mailmind.mailbox.adapters.imap.imaplib_session import ImaplibSession
from mailmind.mailbox.application.bodies import Bodies
from mailmind.mailbox.application.digest import Digest
from mailmind.mailbox.application.commands import MailCommands
from mailmind.mailbox.application.copies import Copies
from mailmind.mailbox.application.filing import Filing
from mailmind.mailbox.application.folders import Folders
from mailmind.mailbox.application.pool import Pool
from mailmind.mailbox.application.queries import Queries
from mailmind.mailbox.application.realtime import Realtime
from mailmind.mailbox.application.reconcile import Reconciler
from mailmind.mailbox.application.stats import LiveStats
from mailmind.mailbox.application.reversal import Reversals
from mailmind.mailbox.application.server_ops import ServerOps
from mailmind.mailbox.application.sync import Ingest
from mailmind.mailbox.application.syncing import Syncing
from mailmind.mailbox.application.warm import Warming
from mailmind.mailbox.application.session import MailSession, ReadOnlySession
from mailmind.platform import bus, profile
from mailmind.platform.db import txguard
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.undo import Undo

from mailmind.bootstrap import accounts         # the connection's TLS, pin and login, until they move into Accounts
from mailmind.bootstrap.accounts import config           # the credential, likewise

store.bind_index(fts)


def _conversations_merged(c, merged):
    """Each context that keeps something under a thread id moves its own rows. A table keyed on a thread id joins here,
    through its owner's rekey_thread. Plain calls on the merge's connection, not the bus: it must commit or roll back
    with the messages it follows, and it must hold for whatever opens the store, wired or not (the command line)."""
    for rekey in (attention_store.rekey_thread, assistant_store.rekey_thread, compose_store.rekey_thread):
        rekey(c, merged.account, merged.survivor, merged.absorbed)


store.bind_merges(_conversations_merged)

KINDS = ("imaplib", "imapclient")
_kind = "imaplib"               # every account's, but one asking for another this process offers
_offered = {"imaplib"}


def offer(kind):
    """Let an account that asks for `kind` have it."""
    if kind not in KINDS:
        raise ValueError(f"no mail session adapter {kind!r}")
    if kind == "imapclient":
        from mailmind.mailbox.adapters.imap import imapclient_session  # noqa: F401  (missing: fail now, not mid-sync)
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


def open_session(acct, timeout=60) -> MailSession:
    """A logged-in MailSession for the account `acct` (a config.accounts() entry). A login its server refused waits out
    its hold (accounts.logins): nothing is sent, and it raises as the refusal did, so each caller does as it did then."""
    if accounts.logins.held(acct):
        raise accounts.LoginUnreachable("a login its server refused is held back")
    return try_login(acct, timeout)


def try_login(acct, timeout=60) -> MailSession:
    """open_session whatever the hold says, for someone who asked. What the server answers counts as any login's does."""
    try:
        session = session_for(acct, timeout)
    except accounts.LoginUnreachable as e:
        if e.refused:
            bus.publish(CredentialsRejected(acct["email"], login_id(acct)))
        raise
    accounts.logins.accepted(acct["email"])
    return session


def session_for(acct, timeout=60) -> MailSession:
    """A session for `acct`, saved or not, as a draft is tried: nothing is asked of a hold, or told of how it went."""
    if kind_for(acct) == "imaplib":
        return ImaplibSession(accounts.connect(acct, timeout=timeout))
    from mailmind.mailbox.adapters.imap import imapclient_session
    txguard.refuse_network("an IMAP connection")
    host, port = acct["host"], acct["port"]
    try:
        session = imapclient_session.connect(
            host, port, starttls=bool(acct["starttls"]), ssl_context=accounts.tls_context(host), timeout=timeout,
            check_tls=lambda imap: accounts.check_pin(acct, host, port, imap),
            username=acct["email"], password=lambda: config.resolve_credential(acct),
            guard=lambda imap: txguard.guard_calls(imap, "send", "IMAP command"))
    except (TLSPinMismatch, imaplib.IMAP4.abort):
        raise
    except BadValue:                     # no server was asked: it would say no every time
        raise
    except imaplib.IMAP4.error as e:     # a login NO/BAD is the connection's fault, not a command's
        raise accounts.login_failed(e) from e
    return session if profile.writes_allowed() else ReadOnlySession(session)


def _open(acct, **kw):
    return open_session(acct, **kw)


class _Accounts:
    def deep_link(self, row, wmap=None):
        """The message in its provider's web client (ProviderProfile.deep_link), else the account's
        webmail (e.g. Nextcloud Mail), else ""."""
        return provider_of({"kind": row["kind"]}).deep_link(
            account=row["account"], message_id=row["message_id"], subject=row["subject"],
            webmail=(wmap or {}).get(row["account"]))

    def alias_map(self):
        return config.alias_map()

    def webmail_map(self):
        return config.webmail_map()

    def signature_map(self):
        return config.signature_map()

    def signature_default(self):
        return config.signature_default()

    """What the Mailbox asks of Accounts."""

    def get(self, email):
        return accounts.get(email)

    def accounts(self):
        return accounts.accounts()

    def trash_folder(self, acct):
        return accounts.trash_folder(acct)

    def profile(self, acct):
        return provider_of(acct)

    def quiet_error(self, e):
        return accounts.quiet_error(e)

    def error_kind(self, e):
        return accounts.error_kind(e)

    def try_login_again(self, email):
        accounts.logins.forget(email)

    def preload_creds(self):
        return accounts.preload_creds()


class _Curation:
    def classify(self, addr, subject, name, has_unsub):
        from mailmind.bootstrap import curation
        return curation.classify(addr, subject, name, has_unsub)

    def is_junk(self, bucket):
        from mailmind.bootstrap import curation
        return curation.is_junk(bucket)

    def by_rule(self, reason):
        from mailmind.bootstrap import curation
        return curation.by_rule(reason)

    def plan_stored(self, acct):
        from mailmind.bootstrap import curation
        return curation.sweeps.plan_stored(acct)

    def buckets(self):
        from mailmind.bootstrap import curation
        return [{"name": b, "is_junk": b in curation.JUNK_BUCKETS} for b in
                [curation.SPAM, curation.BLOCKED, curation.MKT, curation.NOTIF,
                 curation.RECEIPT, curation.PROTECTED, curation.PERSONAL]]


class _Attention:
    def snooze(self, body):
        from mailmind.bootstrap import attention
        return attention.snoozing.snooze(body)

    def on_ingest(self, c, account):
        from mailmind.bootstrap import attention
        return attention.muting.on_ingest(c, account)

    def resolve_followups(self, c, account):
        from mailmind.bootstrap import attention
        return attention.book.resolve_followups(c, account)

    def muted_count(self, c, account):
        from mailmind.bootstrap import attention
        return attention.book.muted_count(c, account)

    def waiting_count(self, c, account):
        from mailmind.bootstrap import attention
        return attention.book.waiting_count(c, account)

    def replylater_count(self, c, account):
        from mailmind.bootstrap import attention
        return attention.book.replylater_count(c, account)

    def list_muted(self, c, account):
        from mailmind.bootstrap import attention
        return attention.book.list_muted(c, account)

    def list_waiting(self, c, account):
        from mailmind.bootstrap import attention
        return attention.book.list_waiting(c, account)

    def waiting_on_others(self, c, accounts, limit):
        from mailmind.bootstrap import attention
        return attention.book.waiting_on_others(c, accounts, limit)

    def list_replylater(self, c, account):
        from mailmind.bootstrap import attention
        return attention.book.list_replylater(c, account)

    def wake_due(self):
        from mailmind.bootstrap import attention
        return attention.snoozing.wake_due()


class _Reader:
    """What another context adds to a message as it opens."""
    def invite_of(self, pk, parsed):
        from mailmind.bootstrap import calendar
        return calendar.invites.of(pk, parsed)

    def draft_info(self, row):
        from mailmind.bootstrap import compose
        return compose.drafts.info(row)


def _reverse_snooze(d):
    from mailmind.bootstrap import attention
    return attention.snoozing.reverse(d)


def _reverse_mute(d):
    from mailmind.bootstrap import attention
    return attention.muting.reverse(d)


def _reverse_block(d):
    from mailmind.bootstrap import curation
    return curation.cleanup.reverse_block(d)


_uow = UnitOfWork()
_directory = _Accounts()
_attention = _Attention()
copies = Copies(_open, imap_quote)
server_ops = ServerOps(_uow, store, _directory, _open, imap_quote)
commands = MailCommands(_uow, store, _directory, server_ops)
pool = Pool(_directory, _open)
ingest = Ingest(_uow, store, _directory, _open, pool, _Curation(), _attention, fts)
warm = Warming(_directory, _open, lambda *a, **kw: ingest.sync_folder(*a, **kw))
realtime = Realtime(_directory, _open)
stats = LiveStats(_directory, _Curation())
folders = Folders(_uow, store, _directory, pool, ingest, _attention)
queries = Queries(_uow, store, _attention, folders, _directory)
bodies = Bodies(_uow, store, ingest, _Reader())
reversals = Reversals(_uow, store, _directory, server_ops, commands, ingest)


def _after_undo():
    stats.invalidate()
    folders.invalidate()            # undo re-added or removed a message: refresh the rail's badges


undo = Undo(_uow, {"move": reversals.move, "gmail_archive": reversals.gmail_archive, "snooze": _reverse_snooze,
                         "mute": _reverse_mute, "unblock": _reverse_block}, _after_undo, _directory.quiet_error)
filing = Filing(_uow, store, _directory, commands, ingest, undo, stats, folders, _attention)


def _kick(job):
    """A job's next round now."""
    from mailmind.bootstrap import background
    background._kick(job)


syncing = Syncing(_uow, store, _directory, ingest, warm, folders, _attention, _kick)
reconciler = Reconciler(_uow, store, _directory, _open, folders, stats)


def _ai_status():
    from mailmind.bootstrap import assistant
    return assistant.calls.status()


digest = Digest(_uow, store, _directory, syncing, _ai_status)
