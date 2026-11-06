"""Binds Accounts. Legacy modules, the other contexts' wiring and the tests reach it through this module:
`config`, the file's directory, and the rest by name, as `accounts.get(email)`."""
from mailmind.accounts.adapters import config_file, imap_connection, secrets
from mailmind.accounts.adapters.imap_connection import LoginUnreachable, ReadOnlyIMAP, is_loopback, tls_context
from mailmind.accounts.application.directory import Directory
from mailmind.accounts.domain import conn_error, provider
from mailmind.mailbox.adapters.imap_names import BadMailbox, imap_quote, mailbox_arg

__all__ = ["BadMailbox", "LoginUnreachable", "ReadOnlyIMAP", "accounts", "check_pin", "config", "quiet_error", "connect", "get",
           "imap_quote", "is_loopback", "mailbox_arg", "preload_creds", "tls_context", "trash_folder"]


def _ai_key():
    from mailmind.bootstrap import assistant
    return assistant.config_key()


config = Directory(config_file.FILE, secrets, _ai_key)
quiet_error = conn_error.quiet
_connector = imap_connection.Connector(config)
connect = _connector.connect
check_pin = _connector.check_pin


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
