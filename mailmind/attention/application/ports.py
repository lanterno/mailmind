"""What Attention needs from outside it. Each is bound in mailmind.bootstrap.attention."""
from typing import Protocol


class AttentionBook(Protocol):
    """Snoozes, mutes and follow-ups, and how a change to them commits. See adapters.sqlite, whose functions take the
    connection `transaction()` gives."""
    def transaction(self): ...


class Messages(Protocol):
    """What only the Mailbox may do to a message's row. `queue_flags` is the SetFlags port: the change reaches the
    server through the queue, in the same transaction."""
    def get(self, c, pk: int): ...
    def queue_flags(self, c, row, flags: dict, mutation_id: str, when: str) -> bool: ...


class Accounts(Protocol):
    def emails(self) -> list: ...


class Undo(Protocol):
    def push(self, entry: dict, group) -> None: ...


class Screen(Protocol):
    """What the running app shows, which a change here makes stale."""
    def stats_changed(self) -> None: ...
    def flags_queued(self) -> None: ...
