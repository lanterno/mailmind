"""What the other contexts' wiring may ask of Accounts by name: what it publishes, what an account's server does, how its
credential and a login are named, and a certificate that changed. What it does, it does through the use cases in
mailmind.bootstrap.accounts."""
from mailmind.accounts.domain.credential import CredentialRef
from mailmind.accounts.domain.events import AccountAdded, AccountChanged, AccountRemoved, CredentialsRejected
from mailmind.accounts.domain.login_hold import login_id
from mailmind.accounts.domain.provider import resolve as provider_of
from mailmind.accounts.domain.tls_pin import TLSPinMismatch

__all__ = ["AccountAdded", "AccountChanged", "AccountRemoved", "CredentialRef", "CredentialsRejected", "TLSPinMismatch", "login_id", "provider_of"]
