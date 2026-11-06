"""Binds Compose. Legacy modules and the command line reach it through this module."""
from mailmind.accounts.domain import tls_pin
from mailmind.compose.adapters.smtp import SmtpTransport


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


transport = SmtpTransport(_Credentials())
