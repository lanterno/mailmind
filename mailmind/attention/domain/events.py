"""What happens to a snooze, a mute or a follow-up, published on mailmind.platform.bus once it has committed."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SnoozeSet:
    """A request hid these many messages until later."""
    count: int


@dataclass(frozen=True)
class SnoozeLifted:
    """A request brought these many back before their time."""
    count: int


@dataclass(frozen=True)
class SnoozeWoke:
    """These many came back on time, in these accounts."""
    count: int
    accounts: tuple[str, ...]


@dataclass(frozen=True)
class ThreadMuted:
    account: str
    thread_id: str


@dataclass(frozen=True)
class ThreadUnmuted:
    account: str
    thread_id: str


@dataclass(frozen=True)
class FollowUpsChanged:
    """The minute's round changed which threads are due, a swap included. `count` is how many are due now."""
    count: int


@dataclass(frozen=True)
class FollowUpDismissed:
    """A person asked this of the thread, whether or not it had a follow-up."""
    account: str
    thread_id: str


@dataclass(frozen=True)
class ReplyLaterChanged:
    """A person pinned the thread to Reply Later, or took it off."""
    account: str
    thread_id: str


@dataclass(frozen=True)
class TasksChanged:
    """A task someone confirmed was added, marked or removed."""
