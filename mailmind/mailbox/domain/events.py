"""What happens to mail (published on mailmind.platform.bus)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Applied:
    """A queued move or flag change reached the server and the local copy matches it."""
    mutation_id: int
    account: str


@dataclass(frozen=True)
class Restored:
    """A queued move or flag change won't happen (the server refused it, the queue gave up on it,
    or its account is gone), and the local row is as the server has it again: a move's message
    listed, a flag change's flags put back."""
    mutation_id: int
    account: str


@dataclass(frozen=True)
class FlagsChanged:
    """A sync found mail read, starred or answered elsewhere."""
    account: str
    folder: str
    count: int


@dataclass(frozen=True)
class InboxChanged:
    """IDLE saw these accounts' INBOX change, a burst of signals coalesced into one. Whoever hears it syncs them."""
    accounts: frozenset[str]
