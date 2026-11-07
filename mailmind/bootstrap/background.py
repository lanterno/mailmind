"""The background jobs: which run in which profile, how often, and who may ask for a round now."""
import os
import time

from mailmind.bootstrap import assistant, attention, compose, curation, mailbox
from mailmind.bootstrap.assistant import answers
from mailmind.bootstrap.mailbox import store
from mailmind.platform import profile
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger
from mailmind.platform.workers import scheduler

SYNC_INTERVAL = 300                       # seconds between automatic syncs
ASSESS_INTERVAL = 300                     # seconds between the assess job's rounds, unless kicked


# Expiry rules (rules.EXPIRE, e.g. "github.com after 72h"): move INBOX mail past its age limit
# to Trash on an own timer. Same shape as reconcile — one account per hold of the gate, taken
# NON-blocking so a live user op is never stalled. Not started under NOSYNC (no IMAP writes).
def _expire_secs():
    try:
        return int(os.environ.get("MAILMIND_EXPIRE_SECS", "3600"))  # 0 disables
    except ValueError:
        get_logger("expire").warning("bad MAILMIND_EXPIRE_SECS — using 3600")
        return 3600


_EXPIRE_INTERVAL = _expire_secs()
_uow = UnitOfWork()


def forget_round():
    """What's kept of a message no copy of has come back to in a month goes, and so do queue rows
    finished a month ago."""
    with _uow.write() as c:
        store.forget_gone_messages(c, int(time.time()))
        store.command_forget_finished(c, int(time.time()))
        answers.batch_forget_finished(c, int(time.time()))


def nosync():
    """Whether this process runs read-only (mailmind.platform.profile: MAILMIND_PROFILE, or the
    older MAILMIND_NOSYNC), with no background sync, reconcile or expiry."""
    return profile.current() is not profile.Profile.FULL


def _kick(job):
    """Run `job`'s next round now, if this process schedules it (read-only schedules no assess)."""
    if job in JOBS.names():
        JOBS.kick(job)


JOBS = scheduler.Scheduler()             # the background jobs; wiring.start puts the running ones here


def schedule(jobs):
    """Put the background work on `jobs`, a Scheduler. Snooze wake always runs, since it needs no
    mail server. Read-only, nothing else does: no sync, triage, backfill, pre-drafting, reconcile
    or expiry."""
    jobs.add("snooze", attention.clocked.round, every=60, first=60)
    if nosync():
        return
    jobs.add("sync", mailbox.syncing.sync_rounds(), every=SYNC_INTERVAL, first=0)
    jobs.add("draft-push", compose.drafts.push_round, every=compose.PUSH_TICK, first=5)    # saves to their servers
    jobs.add("assess", assistant.assessments.round, every=ASSESS_INTERVAL, first=20)         # triage; a sync with new mail kicks it
    jobs.add("backfill", mailbox.syncing.backfill_round, every=600, first=45)              # old mail, full-text searchable
    if not os.environ.get("MAILMIND_NO_DRAFT_TICK"):
        jobs.add("draft-ahead", assistant.drafting.rounds(), every=assistant.drafting.DRAFT_TICK_INTERVAL,
                 first=assistant.drafting.DRAFT_TICK_STARTUP_DELAY)                           # top-K reply pre-gen (AI-polite)
    if mailbox.reconciler._RECONCILE_INTERVAL > 0:                                            # ghost prune, one account a round
        jobs.add("reconcile", mailbox.reconciler.reconcile_rounds(), every=max(30, mailbox.reconciler._RECONCILE_INTERVAL),
                 first=min(120, mailbox.reconciler._RECONCILE_INTERVAL))
    if _EXPIRE_INTERVAL > 0:                                               # the expiry rules
        jobs.add("expire", curation.expiry.round, every=_EXPIRE_INTERVAL, first=min(180, _EXPIRE_INTERVAL))
    jobs.add("forget", forget_round, every=86400, first=600)               # keys with no copy left
