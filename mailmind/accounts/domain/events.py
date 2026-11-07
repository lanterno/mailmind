"""What happens to an account, published on mailmind.platform.bus once the configuration file
holds it, or once its server has answered. Each names the account by its email address, and says
nothing of its credential."""
from dataclasses import dataclass


@dataclass(frozen=True)
class AccountAdded:
    email: str


@dataclass(frozen=True)
class AccountChanged:
    """Its settings changed: endpoint, credential, alias, signature or the like."""
    email: str


@dataclass(frozen=True)
class AccountRemoved:
    email: str


@dataclass(frozen=True)
class CredentialsRejected:
    """Its server answered a login with no. `login` is which login that was (login_hold.login_id)."""
    email: str
    login: str
