"""A reply made ready ahead of time: which one it is, which mail may have one, when a stored one may be served, and how
drafting ahead backs off. The rules alone, with no I/O: `application.prepared_replies` reads and writes the `draft_ahead`
table and asks these.

A stored row is served only for the thread as it is now and while it is young. Its fingerprint is the correctness gate,
and it is taken before the generation, so a thread that changes meanwhile costs another generation and never a stale
serve. Whether a message is worth drafting for ahead of time (`kernel.attention.reply_worthy`) gates what is spent, not
what is served: a draft someone already paid for stays good whatever the verdict says now.
"""
from collections.abc import Callable
from dataclasses import dataclass

from mailmind.kernel.row import Row

TTL = 24 * 3600         # servable age; the fingerprint is the correctness gate, this is hygiene against temporal drift
FAIL_TTL = 60           # after a failure, opening the message won't ask again for this long (a person recovers fast)
FAIL_BASE = 300         # the background round's backoff after the 1st failure: 5 min...
FAIL_CAP = 6 * 3600     # ...doubling per consecutive failure, capped at 6h (persisted in the table)
TRIP = 3                # consecutive generations that failed, for the round's breaker...
SUSPEND = 1800          # ...which stays open for 30 min (an AI outage)


def key_of(row: Row) -> tuple[str, str] | None:
    """A prepared reply's identity, (account, 't:<thread>' or 'm:<Message-ID>'), which survives a folder being renumbered,
    so never a pk. By thread, so two messages of one can't each pay for one. None for malformed mail with neither: nothing
    is drafted ahead for it, and a draft asked for still works."""
    tid = (row["thread_id"] or "").strip()
    if tid:
        return (row["account"], "t:" + tid)
    mid = (row["message_id"] or "").strip()
    if mid:
        return (row["account"], "m:" + mid)
    return None


def eligible(row: Row | None, looks_human: Callable[[str], bool]) -> bool:
    """Replyable to a real human, and not already handled. Your own message isn't: a thread ending in a Sent copy has
    nothing to answer. `looks_human` is Curation's word on a sender's local part (no-reply, notifications, role senders)."""
    if not row or row["is_junk"] or row["answered"] or row["snooze_until"]:
        return False
    if (row["from_addr"] or "").lower() == row["account"].lower() or "sent" in (row["folder"] or "").lower():
        return False
    local = (row["from_addr"] or "").split("@")[0].lower()
    return bool(local) and looks_human(local)


def for_the_anchor(pk: int, thread_id: str | None, anchor_pk: int | None) -> bool:
    """A thread's prepared reply answers its anchor, so it never pairs with a composer quoting an older message. A message
    with no thread is its own."""
    return not thread_id or pk == anchor_pk


def servable(stored: Row | None, fp: str, now: int) -> bool:
    """A stored draft for the mail as it is now: it has a body, the fingerprint matches, and it is under TTL old."""
    return bool(stored and stored["body"] and stored["fp"] == fp and now - stored["created_ts"] < TTL)


def failed_lately(stored: Row | None, now: int) -> bool:
    """Opening the message shouldn't ask again so soon after a failure. The person falls back to asking for one."""
    return bool(stored and stored["fail_ts"] and now - stored["fail_ts"] < FAIL_TTL)


def backing_off(stored: Row | None, now: int) -> bool:
    """The background round's backoff, which outlives a restart: 5 minutes after one failure, doubling to 6 hours."""
    if not stored or not stored["fail_ts"]:
        return False
    wait = min(FAIL_CAP, FAIL_BASE * (2 ** max(0, stored["fail_count"] - 1)))
    return now - stored["fail_ts"] < wait


def default_shaped(instruction: str, tone: str) -> bool:
    """A reply asked for with no instruction and the default tone: what a prepared one is, so it can answer such a request
    and be kept from one. Anything else is a personal one-off."""
    return not instruction.strip() and tone == "balanced"


@dataclass
class Breaker:
    """The background round's three strikes: generations it led that failed, in a row, stop it for SUSPEND seconds. Nothing
    keeps this across a restart: a round has the persisted backoff for that."""
    fails: int = 0
    open_until: float = 0.0

    def is_open(self, now: float) -> bool:
        return now < self.open_until

    def heard(self, outcome: bool | None, now: float) -> bool:
        """What a generation came to: True led and worked, False led and failed, None neither (it joined another's, or found
        nothing to do), which leaves the count alone. True when this one tripped the breaker."""
        if outcome is True:
            self.fails = 0
        elif outcome is False:
            self.fails += 1
        if self.fails < TRIP:
            return False
        self.open_until, self.fails = now + SUSPEND, 0
        return True
