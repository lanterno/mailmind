"""What happens to mail. All but ConversationsMerged are published on mailmind.platform.bus, once the change has committed."""
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


@dataclass(frozen=True)
class ConversationsMerged:
    """Every message of thread `absorbed` now holds `survivor`: two threads joined, or one gained a Gmail thread id.
    Whoever keeps something under a thread id moves it inside the transaction that rewrote the messages, on its
    connection, so it never goes through the bus, whose handlers run after the commit and may fail on their own."""
    account: str
    survivor: str
    absorbed: str
