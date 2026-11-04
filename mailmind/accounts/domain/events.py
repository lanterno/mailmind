"""What happens to an account, published on mailmind.platform.bus once the configuration file
holds it. Each names the account by its email address and nothing more."""
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
