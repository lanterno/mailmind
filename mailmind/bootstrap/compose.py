"""Binds Compose. Legacy modules and the command line reach it through this module: `sending`, `drafts`, `composing`,
`outbox`, `transport`, `snippets`, and `book`, its tables."""
import imaplib

from mailmind.accounts.api import TLSPinMismatch, provider_of
from mailmind.bootstrap.mailbox import copies
from mailmind.compose.adapters import snippets_file
from mailmind.compose.adapters import sqlite as book
from mailmind.compose.adapters.smtp import SmtpTransport
from mailmind.compose.application.composing import Composing
from mailmind.compose.application.drafts import Drafts
from mailmind.compose.application.outbox import Outbox
from mailmind.compose.application.sending import Sending
from mailmind.compose.application.snippets import Snippets
from mailmind.compose.domain.draft_journal import PUSH_TICK
from mailmind.mailbox.domain import roles
from mailmind.platform import sse
from mailmind.platform.db.uow import UnitOfWork
from mailmind.bootstrap import mailbox
from mailmind.bootstrap import accounts

__all__ = ["PUSH_TICK", "book", "composing", "drafts", "outbox", "sending", "snippets", "transport"]

class _Credentials:
    """An account's TLS and its secret, until Accounts holds them."""
    PinMismatch = TLSPinMismatch

    def tls_context(self, host):
        from mailmind.bootstrap import accounts
        return accounts.tls_context(host)

    def check_pin(self, acct, host, port, connection):
        from mailmind.bootstrap import accounts
        accounts.check_pin(acct, host, port, connection)

    def secret(self, acct):
        from mailmind.bootstrap.accounts import config
        return config.resolve_credential(acct)


class _Accounts:
    """An account as the configuration has it, until Accounts holds it."""
    def get(self, email):
        from mailmind.bootstrap import accounts
        return accounts.get(email)              # host, port and credential only from the configuration

    def saves_sent(self, acct):
        return provider_of(acct).saves_sent

    def undo_send_seconds(self):
        """A file that can't be read leaves a send at the default, as it does the AI's daily limit: mail still goes."""
        from mailmind.bootstrap import accounts
        try:
            return accounts.config.preference("undo_send_seconds")
        except (OSError, ValueError):
            return None


class _Messages:
    """A message's row and its body, until the Mailbox holds them."""
    def original(self, pk, account=None, message_id=None):
        """The row of the message a send replies to: by Message-ID, since a pk may have been renumbered,
        else by the pk it was queued with. None once mail has moved out of every folder stored."""
        from mailmind.bootstrap.mailbox import stored_mail
        with _uow.read() as c:
            return stored_mail.get_message_by_msgid(c, account, message_id) or (stored_mail.get_message(c, pk) if pk else None)

    def message(self, pk):
        from mailmind.bootstrap.mailbox import stored_mail
        with _uow.read() as c:
            return stored_mail.get_message(c, pk)

    def by_msgid(self, account, message_id):
        from mailmind.bootstrap.mailbox import stored_mail
        with _uow.read() as c:
            return stored_mail.get_message_by_msgid(c, account, message_id)

    def by_key(self, account, key):
        from mailmind.bootstrap.mailbox import stored_mail
        with _uow.read() as c:
            return stored_mail.get_message_by_key(c, account, key)

    def body(self, pk):
        from mailmind.bootstrap.mailbox import stored_mail
        with _uow.read() as c:
            return stored_mail.get_body(c, pk)

    def uidvalidity(self, account, folder):
        from mailmind.bootstrap.mailbox import stored_mail
        with _uow.read() as c:
            return stored_mail.get_uidvalidity(c, account, folder)

    def remove(self, pk):
        from mailmind.bootstrap.mailbox import stored_mail
        with _uow.write() as c:
            stored_mail.delete_message(c, pk)

    def remove_copies(self, c, account, folder, uids):
        from mailmind.bootstrap.mailbox import stored_mail
        stored_mail.delete_by_uids(c, account, folder, uids)

    def fetch_body(self, pk):
        from mailmind.bootstrap.mailbox import ingest
        return ingest.fetch_body(pk)

    def sync_whole(self, acct, folder):
        """Full: only a full scan brings back older UIDs."""
        from mailmind.bootstrap.mailbox import ingest
        return ingest.sync_folder(acct, folder, full=True)

    def unreachable(self):
        """What says a draft couldn't be read now, rather than that it can't be read at all."""
        from mailmind.bootstrap.mailbox import commands
        return commands.UNREACHABLE + (imaplib.IMAP4.error,)


class _Effects:
    """What a delivered send sets off elsewhere."""
    def mark_answered(self, pk, account, message_id):
        from mailmind.bootstrap.mailbox import server_ops
        return server_ops.mark_answered(pk, account, message_id)

    def followup_sent(self, c, account, thread_id, now, at):
        from mailmind.bootstrap import attention
        attention.book.followup_record_sent(c, account, thread_id, now, at)


class _Screen:
    """What the server keeps about folders and tells the page, until the Mailbox and the routers hold it."""
    def names_drafts(self, folder):
        return roles.names_drafts(folder)

    def plays_drafts(self, account, folder):
        return mailbox.folders.plays_drafts(account, folder)

    def quiet_error(self, e):
        return accounts.quiet_error(e)

    def touched(self, account, folder):
        mailbox.folders.touched(account, folder)

    def list_changed(self, account, folder):
        mailbox.folders.list_changed(account, folder)

    def outbox_changed(self):
        sse.publish("outbox", {})

    def stats_changed(self):
        mailbox.stats.invalidate()


transport = SmtpTransport(_Credentials())
_accounts, _mail, _screen = _Accounts(), _Messages(), _Screen()
_uow = UnitOfWork()
outbox = Outbox(_uow, book, transport, copies, _accounts, _mail, _Effects(), _screen)
composing = Composing(_uow, book, _accounts, _mail, _screen)
sending = Sending(_uow, composing, outbox, book)
drafts = Drafts(_uow, composing, outbox, book, _accounts, _mail, copies, _screen)
snippets = Snippets(snippets_file)
