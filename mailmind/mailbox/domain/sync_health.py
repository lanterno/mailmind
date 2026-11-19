"""When an account's failed sync shows as one that can't connect. Values in and values out: nothing here asks a server,
the database or the clock.

Trouble that may pass (Accounts says which: a connection lost or refused, a server that didn't answer in time or can't
take a login for now) is what a sleep or a change of network leaves for a moment: it shows only once the next sync of the
account has failed too, or once the first failure in a row is three minutes old. Any other, a password refused or a
certificate changed, shows at once. A sync that works ends the run. A run may begin before any sync has failed, with a
connection to the server that couldn't be made outside one (`heard`): it counts from then.

While a run of trouble that may pass lasts, the account's server is tried again every RETRY_SECONDS, so the sync that
ends it comes within moments of the server answering again, not at the next round, minutes on. A try that fails changes
nothing: it is no sync of the round's, and counts toward nothing. The Rust core's `mm_mailbox::sync_health` is the same.
"""
from dataclasses import dataclass

SHOWS_AFTER_ROUNDS = 2          # the failed syncs in a row at which trouble that may pass shows
SHOWS_AFTER_SECONDS = 180       # the seconds since the first failure in a row at which it shows
RETRY_SECONDS = 20              # the seconds between tries of an account's server while its trouble may pass


@dataclass(frozen=True)
class Failing:
    """An account's syncs failed in a row, until one works: how many, when the first failed, the last's class, and
    whether it may pass."""
    rounds: int
    since: float
    error: str
    passing: bool

    def shows(self, now: float) -> bool:
        """Whether the run shows as one that can't connect, at `now`."""
        return shows(self.passing, self.rounds, now - self.since)


def and_one_more(run: Failing | None, error: str, passing: bool, now: float) -> Failing:
    """The run with one more failure, at `now`: the first of a run when there was none."""
    if run is None:
        return Failing(1, now, error, passing)
    return Failing(run.rounds + 1, run.since, error, passing)


def heard(error: str, now: float) -> Failing:
    """A run begun at `now` by trouble that may pass, heard outside a sync (a connection to the server that couldn't be
    made): no sync has failed in it yet, so the next that fails is its first."""
    return Failing(0, now, error, True)


def shows(passing: bool, rounds: int, since: float) -> bool:
    """Whether a failure, the `rounds`th in a row, the first `since` seconds ago, shows: at once, unless it may pass."""
    return not passing or rounds >= SHOWS_AFTER_ROUNDS or since >= SHOWS_AFTER_SECONDS
