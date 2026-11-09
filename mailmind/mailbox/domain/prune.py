"""What a reconcile may take away: the rows of mail no longer on the server. Each rule here keeps a server's odd answer from
deleting mail that is there. Values in and values out: nothing asks a server or the database.

- A folder the server renumbered (UIDVALIDITY changed) can't be compared at all: its live UIDs are in another numbering, and
  every stored row would look gone. A sync sees the change and starts the folder again.
- An empty SEARCH against a folder with mail stored says nothing is there, which is far likelier a bad answer than the truth.
  Only a person forcing it prunes on that.
- A row a sync stored after the SEARCH isn't in its answer, and isn't gone, so only what was stored before it may go.
"""
from collections.abc import Collection

EVERY_AT_LEAST = 30             # seconds between one account's reconcile and the next, however many there are


def renumbered(server: int | None, stored: int | None) -> str | None:
    """Why this folder can't be pruned, the server having renumbered it, or None when it hasn't, as far as either side says."""
    if server and stored and server != stored:
        return f"UIDVALIDITY changed ({stored}->{server}) — prune skipped"
    return None


def empty_search(live: Collection[int], stored: int, force: bool) -> str | None:
    """Why this folder can't be pruned, its SEARCH having found nothing though mail is stored; None when it can."""
    if not live and stored > 0 and not force:
        return "empty SEARCH against non-empty store — skipped"
    return None


def ghosts(stored_uids: Collection[int], live: Collection[int]) -> int:
    """How many stored rows the server no longer holds."""
    return sum(1 for u in stored_uids if u not in live)


def goes(uid: int, live: Collection[int], among: Collection[int] | None) -> bool:
    """Whether the row at `uid` is pruned. `among`, when given, is what was stored before the SEARCH that said `live`."""
    return uid not in live and (among is None or uid in among)


def next_round(interval: int, accounts: int) -> int:
    """Seconds until the next account's round, spreading the full `interval` across the `accounts`."""
    return max(EVERY_AT_LEAST, interval // (accounts or 1))
