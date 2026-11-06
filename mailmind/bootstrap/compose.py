"""Binds Compose. Legacy modules and the command line reach it through this module: `outbox`, `transport`,
and `book`, its tables."""
from mailmind.accounts.domain import provider, tls_pin
from mailmind.bootstrap.mailbox import copies
from mailmind.compose.adapters import sqlite as book
from mailmind.compose.adapters.smtp import SmtpTransport
from mailmind.compose.application.outbox import Outbox
from mailmind.mailbox.domain import roles

__all__ = ["book", "outbox", "transport"]


class _Credentials:
    """An account's TLS and its secret, until Accounts holds them."""
    PinMismatch = tls_pin.TLSPinMismatch

    def tls_context(self, host):
        import accounts
        return accounts.tls_context(host)

    def check_pin(self, acct, host, port, connection):
        import accounts
        accounts.check_pin(acct, host, port, connection)

    def secret(self, acct):
        import config
        return config.resolve_credential(acct)


class _Accounts:
    """An account as the configuration has it, until Accounts holds it."""
    def get(self, email):
        import accounts
        return accounts.get(email)              # host, port and credential only from the configuration

    def saves_sent(self, acct):
        return provider.resolve(acct).saves_sent


class _Messages:
    """A message's row and its body, until the Mailbox holds them."""
    def original(self, pk, account=None, message_id=None):
        """The row of the message a send replies to: by Message-ID, since a pk may have been renumbered,
        else by the pk it was queued with. None once mail has moved out of every folder stored."""
        import store
        c = store.conn()
        try:
            return store.get_message_by_msgid(c, account, message_id) or (store.get_message(c, pk) if pk else None)
        finally:
            c.close()

    def remove_copies(self, c, account, folder, uids):
        import store
        store.delete_by_uids(c, account, folder, uids)


class _Effects:
    """What a delivered send sets off elsewhere."""
    def account_of(self, pk):
        import mailops
        return mailops.account_of(pk)

    def mark_answered(self, pk, account, message_id):
        import mailops
        return mailops.mark_answered(pk, account, message_id)

    def followup_sent(self, c, account, thread_id, pk, now, at):
        from mailmind.bootstrap import attention
        attention.book.followup_record_sent(c, account, thread_id, pk, now, at)


class _Screen:
    """What the server keeps about folders and tells the page, until the Mailbox and the routers hold it."""
    def names_drafts(self, folder):
        return roles.names_drafts(folder)


transport = SmtpTransport(_Credentials())
_accounts, _mail, _screen = _Accounts(), _Messages(), _Screen()
outbox = Outbox(book, transport, copies, _accounts, _mail, _Effects(), _screen)
