"""How the body backfill shares its rounds, and what a failure costs: the BodyBackfillPolicy. Values in and values out:
nothing here asks a server, the database or the clock.

A round asks each account in turn, each one's newest first, so no account's backlog, outage or busy gate holds another's
mail back. A failure sets its message aside, so the next try at that account reaches the mail behind it:

- A body the server didn't give (deleted elsewhere and not yet pruned here, or refused) waits NOT_GIVEN_WAIT.
- A connection lost over a message doesn't say which was at fault. A server that is down, and a message it can't serve (one
  that outlasts the timeout, or breaks the server each time), look alike from here, so each pays a little. The account
  rests for UNREACHABLE_REST and is asked nothing more until then. The message waits a minute, then twice that each time, up
  to NOT_GIVEN_WAIT, so one that no try can fetch costs its account a turn less and less often, and an outage costs each
  message it fell on no more than that message's wait.

The Mailbox keeps what this hands back in memory: a restart tries everything again, which costs a few turns and nothing else.
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

ROUND = 12                      # how many bodies a round asks for
BACKLOG_WAIT = 8                # seconds to the next round while there is mail left to ask for
CAUGHT_UP_WAIT = 600            # with none: new mail lands body-less
NOT_GIVEN_WAIT = 6 * 3600
UNREACHABLE_REST = 60


@dataclass(frozen=True)
class SetAside:
    """A message a round isn't to ask for until `until`."""
    until: float
    connections_lost: int = 0   # in a row, over this message


def not_given(now: float) -> SetAside:
    """The server answered, and gave no body."""
    return SetAside(now + NOT_GIVEN_WAIT)


def connection_lost(was: SetAside | None, now: float) -> SetAside:
    """The connection went while asking for this message's body, `was` being how it stood before."""
    lost = (was.connections_lost if was else 0) + 1
    return SetAside(now + min(NOT_GIVEN_WAIT, UNREACHABLE_REST * 2 ** (lost - 1)), lost)


def still_waiting(set_aside: Mapping[int, SetAside], now: float) -> list[int]:
    """The messages a round leaves out at `now`. One whose wait is over stays in `set_aside`, which remembers its count."""
    return [pk for pk, one in set_aside.items() if one.until > now]


def in_turn[T](waiting: Sequence[Sequence[T]], limit: int = ROUND) -> list[T]:
    """What a round asks for, in order, of what each account has `waiting`: each one's first, then each one's second, and
    so on, up to `limit`."""
    turns = (rows[i] for i in range(limit) for rows in waiting if i < len(rows))
    return [one for one, _ in zip(turns, range(limit))]


def next_round(*, going: bool, resting: bool) -> int:
    """Seconds until the next round. `going`: an account has mail left to ask for and isn't resting. `resting`: one is."""
    if going:
        return BACKLOG_WAIT
    return UNREACHABLE_REST if resting else CAUGHT_UP_WAIT
