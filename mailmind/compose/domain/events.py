"""What Compose publishes on mailmind.platform.bus."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Sent:
    """A queued send went out, or a later try at its Sent copy ended."""
    outbox_id: int
    account: str


@dataclass(frozen=True)
class DraftCopyRemoved:
    """A sent message's draft left the server and the list."""
    account: str
    folder: str


@dataclass(frozen=True)
class Failed:
    """An attempt at a queued send failed: it waits for its next try, or, `gave_up`, in error for
    a person to retry it."""
    outbox_id: int
    account: str
    gave_up: bool
