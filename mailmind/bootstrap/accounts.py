"""Binds Accounts. Legacy modules, the other contexts' wiring and the tests reach it through this module:
`config`, the file's directory, and the rest by name, as `accounts.get(email)`."""
from mailmind.accounts.adapters import config_file, imap_connection, secrets
from mailmind.accounts.adapters.imap_connection import LoginUnreachable, ReadOnlyIMAP, login_failed, tls_context
from mailmind.accounts.application.connections import Connections
from mailmind.accounts.application.directory import Directory
from mailmind.accounts.application.health import Health
from mailmind.accounts.application.logins import Logins
from mailmind.accounts.domain import conn_error, provider
from mailmind.accounts.domain.health import error_kind
from mailmind.mailbox.adapters.imap.imap_names import BadMailbox, imap_quote, mailbox_arg

__all__ = ["BadMailbox", "LoginUnreachable", "ReadOnlyIMAP", "accounts", "check_pin", "config", "connections", "error_kind", "health", "login_failed", "logins", "quiet_error", "connect", "get",
           "imap_quote", "mailbox_arg", "preload_creds", "tls_context", "trash_folder"]


def _ai_key():
    from mailmind.bootstrap import assistant
    return assistant.config_key()


quiet_error = conn_error.quiet


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


class _Probe:
    """The login goes through the Mailbox's sessions, which pick the IMAP adapter, so this is no adapter of Accounts'
    own: it would have to import another context's."""

    def saved(self, acct, timeout):
        from mailmind.bootstrap import mailbox
        self._leave(mailbox.try_login(acct, timeout=timeout))

    def draft(self, acct, timeout):
        from mailmind.bootstrap import mailbox
        self._leave(mailbox.session_for(acct, timeout=timeout))

    def _leave(self, session):
        try:
            session.logout()
        except Exception:
            pass


def _retry_sync(body):
    from mailmind.bootstrap import mailbox
    return mailbox.syncing.do_sync_retry(body)


def _forget_status(email):
    from mailmind.bootstrap import mailbox
    mailbox.syncing._status.pop(email, None)


def _sync_record():
    from mailmind.bootstrap import mailbox
    return mailbox.syncing.sync_record()


def connect(acct, timeout=60):
    """An imaplib connection to `acct`'s server, logged in (imap_connection.Connector)."""
    return _connector.connect(acct, timeout=timeout)


def check_pin(acct, host, port, imap):
    return _connector.check_pin(acct, host, port, imap)


def assemble():
    """Accounts' objects, made afresh: the file's directory, the connector, who has been refused a login, and no secret
    read yet. Importing this module assembles them once; wiring.assemble makes a new set of every context's."""
    global config, _connector, connections, health, logins
    secrets.forget()
    config = Directory(config_file.FILE, secrets, _ai_key)
    _connector = imap_connection.Connector(config)
    connections = Connections(config, get, accounts, _Probe(), quiet_error, _retry_sync, _forget_status)
    health = Health(_sync_record)
    logins = Logins()


assemble()
