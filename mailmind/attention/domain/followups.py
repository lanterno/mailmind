"""A follow-up: a thread that waits on someone, and how long it may.

A thread has at most one, kept by account and thread id, live or not. Its kind says who owes what: `sent` (you wrote and
they haven't answered), `received` (they wrote and you haven't) or `replylater` (you pinned it to answer). It is `pending`,
then `due`, unless a person has `dismissed` it, and a dismissed one stays so (decision D12).

The minute's round asks these over every thread at once, so it runs as SQL. Each table below names a rule's conditions and
states each one over the values here, and adapters.sqlite builds its statement from those names, saying how each reads
there. So a condition joins or leaves a rule in this file, and tests/unit/attention/test_followups_in_sql.py runs both over
the same threads.
"""
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Final

GRACE_DAYS = 3      # grace window before a sent-no-reply / unanswered thread surfaces as "Waiting"
EXPIRE_DAYS = 14    # cap how long a received nudge nags (a reply from another client is locally invisible)
_DAY = 86400

SENT: Final = "sent"
RECEIVED: Final = "received"
REPLY_LATER: Final = "replylater"
PENDING: Final = "pending"
DUE: Final = "due"
DISMISSED: Final = "dismissed"
LIVE: Final = (PENDING, DUE)


def days_before(now: int, days: int) -> int:
    return now - days * _DAY


@dataclass(frozen=True)
class FollowUp:
    """One thread's row."""
    kind: str
    state: str
    first_sent_ts: int | None = None
    last_msg_ts: int | None = None
    due_ts: int | None = None
    created_at: str | None = None
    resolved_at: str | None = None

    @property
    def live(self) -> bool:
        return self.state in LIVE


@dataclass(frozen=True)
class Mail:
    """A message of a thread, as far as a follow-up goes by it. `mine` is from the account itself, and `reply_worthy` what
    kernel.attention says of its verdict."""
    pk: int
    ts: int
    mine: bool = False
    draft: bool = False
    seen: bool = True
    answered: bool = False
    junk: bool = False
    snoozed: bool = False
    reply_worthy: bool = False


@dataclass(frozen=True)
class Thread:
    """A thread as it stands: its copies, every folder's, and whether it is muted or has a follow-up already."""
    id: str | None
    mail: tuple[Mail, ...] = ()
    muted: bool = False
    follow_up: FollowUp | None = None

    @property
    def newest(self) -> Mail | None:
        """Its newest message that isn't a draft, the higher pk on a tie. A draft of a reply is no reply."""
        sent_or_received = [m for m in self.mail if not m.draft]
        return max(sent_or_received, key=lambda m: (m.ts, m.pk)) if sent_or_received else None

    def i_replied(self) -> bool:
        """A reply of mine here or from another client, by its Sent copy or by the server's \\Answered."""
        return any(m.answered or (m.mine and not m.draft) for m in self.mail)

    def answered_since(self, ts: int) -> bool:
        """Someone else has written since then."""
        return any(not m.mine and m.ts > ts for m in self.mail)


# A `received` nudge: they wrote, it has been read, nobody has answered and it matters. Held over the message it would
# anchor on, which must be its thread's newest. It waits out the grace window. The floor is the nudge's own lifetime, so
# one that expired can't come straight back and a first run can't flood; a thread with any follow-up already, a dismissed
# one included, gets none (decision D12).
RECEIVED_NUDGE: Final[Mapping[str, Callable[[Thread, Mail, int], bool]]] = {
    "in_a_thread": lambda t, m, now: bool(t.id),
    "read": lambda t, m, now: m.seen,
    "unanswered": lambda t, m, now: not m.answered,
    "not_junk": lambda t, m, now: not m.junk,
    "past_the_grace": lambda t, m, now: m.ts <= days_before(now, GRACE_DAYS),
    "within_its_lifetime": lambda t, m, now: m.ts >= days_before(now, EXPIRE_DAYS),
    "the_threads_newest": lambda t, m, now: m == t.newest,
    "not_snoozed": lambda t, m, now: not m.snoozed,
    "worth_a_reply": lambda t, m, now: any(x.reply_worthy for x in t.mail),
    "nothing_from_me": lambda t, m, now: not any(x.mine and not x.draft for x in t.mail),
    "not_muted": lambda t, m, now: not t.muted,
    "no_follow_up_yet": lambda t, m, now: t.follow_up is None,
}

# A `sent` follow-up comes due: the grace window has gone by with no answer, and the thread isn't muted.
COMES_DUE: Final[Mapping[str, Callable[[FollowUp, Thread, int], bool]]] = {
    "sent_and_pending": lambda f, t, now: f.kind == SENT and f.state == PENDING,
    "past_the_grace": lambda f, t, now: f.first_sent_ts is not None and f.first_sent_ts <= days_before(now, GRACE_DAYS),
    "no_answer_since": lambda f, t, now: not t.answered_since(f.first_sent_ts or 0),
    "not_muted": lambda f, t, now: not t.muted,
}

# A `received` nudge ends when it has nagged long enough: a reply from another client can't be seen here until Sent syncs.
# Only a live one ends, so a dismissal stays, and strictly past its lifetime, where detection's floor is inclusive.
EXPIRES: Final[Mapping[str, Callable[[FollowUp, int], bool]]] = {
    "received": lambda f, now: f.kind == RECEIVED,
    "live": lambda f, now: f.live,
    "past_its_lifetime": lambda f, now: f.last_msg_ts is not None and f.last_msg_ts < days_before(now, EXPIRE_DAYS),
}

# What meets a live follow-up's commitment, any of these. Only a live one is met, so a dismissal stays. A sent one is met
# by someone answering, the others by you replying; a draft of a reply is no reply.
MET: Final[Mapping[str, Callable[[FollowUp, Thread], bool]]] = {
    "muted": lambda f, t: t.muted,
    "they_answered": lambda f, t: f.kind == SENT and t.answered_since(f.first_sent_ts or 0),
    "i_replied": lambda f, t: f.kind in (RECEIVED, REPLY_LATER) and t.i_replied(),
}


def nudge_for(thread: Thread, now: int) -> Mail | None:
    """The message a `received` nudge on this thread would anchor on, if it wants one now."""
    return next((m for m in thread.mail if all(holds(thread, m, now) for holds in RECEIVED_NUDGE.values())), None)


def comes_due(f: FollowUp, thread: Thread, now: int) -> bool:
    return all(holds(f, thread, now) for holds in COMES_DUE.values())


def expired(f: FollowUp, now: int) -> bool:
    return all(holds(f, now) for holds in EXPIRES.values())


def met(f: FollowUp, thread: Thread) -> bool:
    return f.live and any(holds(f, thread) for holds in MET.values())


def on_reply_sent(current: FollowUp | None, now: int, when: str) -> FollowUp | None:
    """A reply went out, so a `sent` one starts, unless the thread has one already, whatever its state: one to a thread,
    and a dismissal stays dismissed. None if there's nothing to write."""
    return None if current else FollowUp(SENT, PENDING, first_sent_ts=now, last_msg_ts=now, created_at=when)


def pinned(current: FollowUp | None, last_msg_ts: int | None, when: str) -> FollowUp:
    """The thread pinned to Reply Later. That supersedes any nudge, since an explicit "I'll reply" beats an aging guess, and
    re-activates a dismissal, which only a person asking may. What it says of when a send went out and came due stays."""
    return replace(current or FollowUp(REPLY_LATER, DUE), kind=REPLY_LATER, state=DUE, last_msg_ts=last_msg_ts, created_at=when,
                   resolved_at=None)


def dismissed(current: FollowUp | None, when: str) -> FollowUp | None:
    """A person dismissed it. The row stays, so that nothing starts another on this thread. With none, nothing to write."""
    return replace(current, state=DISMISSED, resolved_at=when) if current else None


def unpinning_ends(current: FollowUp | None) -> bool:
    """Un-pinning ends only a pin: a nudge on the thread isn't the stack's to take."""
    return current is not None and current.kind == REPLY_LATER
