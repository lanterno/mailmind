"""What a sync of one folder asks the server for: the SyncPlanner. Values in and values out, from what the select said and
what is stored: Ingest does the asking, and the storing.

The new mail is a UID range above the folder's watermark, or the whole folder on a first scan, a full one, or once the
server has renumbered it (UIDVALIDITY changed). A scan says how much it read in the folder's scan_state: `recent` when cut
to the newest N, `synced` when whole. An incremental sync leaves that as it was.

The flags of mail already stored go by the folder's HIGHESTMODSEQ where the server keeps one (CONDSTORE). The same as the
mark the last read left, and nothing changed. Past it, the server names what changed, however old. With no mark to compare,
no modseq, or a server that won't say, the FLAG_REFRESH newest stored are read.
"""
from collections.abc import Sequence
from dataclasses import dataclass

FLAG_REFRESH = 500              # the newest stored UIDs whose flags an incremental sync reads, with nothing better to go by
WHOLE, CUT = "synced", "recent"  # the scan_state a scan leaves


@dataclass(frozen=True)
class FolderPlan:
    reset: bool                 # the server renumbered the folder: what is stored of it goes first
    whole: bool                 # every UID, not only those above `above`
    above: int                  # the folder's watermark
    recent: int | None          # a whole scan keeps only the newest this many
    read_flags: bool            # stored mail is known, so its flags may have changed since

    def arrives_in(self, folder: str) -> bool:
        """Whether what this finds is new to the folder, so a person's filters speak for it. A first or a whole scan reads
        what was already there."""
        return not self.whole and reads_bodies(folder)

    @property
    def search(self) -> tuple[str, ...]:
        """The UID SEARCH that names the mail to fetch."""
        return ("ALL",) if self.whole else ("UID", f"{self.above + 1}:*")

    def to_fetch(self, found: list[int]) -> tuple[list[int], str | None]:
        """Of the UIDs `search` found: which to fetch, and the scan_state fetching them leaves, or None to leave it be."""
        if not self.whole:
            return [u for u in found if u > self.above], None     # N:* always matches the last UID, even below N
        if self.recent and len(found) > self.recent:
            return found[-self.recent:], CUT
        return found, WHOLE


def reads_bodies(folder: str) -> bool:
    """Whether a sync of this folder fetches bodies as it goes: the Inbox's, for the AI's snippet. Other folders fetch one as
    it opens."""
    return folder.upper() == "INBOX"


def asked_again(asked: Sequence[frozenset[str] | None]) -> set[str] | None:
    """What the next sync round covers, of the accounts each ask named while one ran; None, every account, wins."""
    return None if None in asked else {e for one in asked if one is not None for e in one}


def catch_up_due(folder: str, *, running: bool, last_started: float | None, now: float, every: float) -> bool:
    """Whether opening a folder already read should catch it up: not INBOX, which the background sync keeps, and not again
    within `every` seconds. An open while one runs waits for it too, or it would answer with what that one is about to change."""
    if folder.upper() == "INBOX":
        return False
    return running or last_started is None or now - last_started >= every


def plan_folder(*, uidvalidity: int | None, known_uidvalidity: int | None, watermark: int, full: bool,
                recent: int | None) -> FolderPlan:
    """What to fetch of a folder, from the UIDVALIDITY its select said and what is stored of it. `full` asks for a whole
    scan, and `recent` bounds one."""
    reset = bool(uidvalidity and known_uidvalidity and uidvalidity != known_uidvalidity)
    whole = full or reset or watermark == 0
    return FolderPlan(reset=reset, whole=whole, above=watermark, recent=recent, read_flags=not whole)


@dataclass(frozen=True)
class FlagPlan:
    modseq: int | None = None           # the HIGHESTMODSEQ the select said, if the server keeps one
    unchanged: bool = False             # it matches the mark, so there is nothing to read
    changed_since: int | None = None    # ask the server what changed since this; None reads the newest stored

    def mark(self, *, held_back: bool, searched: bool) -> int | None:
        """What this read leaves as the folder's mark, or None to leave the mark where it is. It stays behind anything the
        read skipped (`held_back`: a change still queued could be refused and need those flags back), and behind a search
        that was asked for and refused (`searched` False), since the newest aren't all that changed."""
        if not self.modseq or held_back or (self.changed_since is not None and not searched):
            return None
        return self.modseq


def plan_flags(modseq: int | None, mark: int | None) -> FlagPlan:
    """How to read the flags of stored mail, from the `modseq` the select just said and the `mark` the last read left."""
    if not modseq:
        return FlagPlan()
    if mark == modseq:
        return FlagPlan(modseq, unchanged=True)
    return FlagPlan(modseq, changed_since=mark if mark and mark < modseq else None)
