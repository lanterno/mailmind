"""What every way of running the app does first.

Each front door calls `start()` before it serves, and the tests' app fixture calls
`wire()`, so all of them put the app together the same way.
"""
import threading

from mailmind.accounts.domain import events as account_events
from mailmind.bootstrap import background, compose, mailbox
from mailmind.compose.api import DraftCopyRemoved, Failed, Sent
from mailmind.platform import bus, profile, sse
from mailmind.platform.workers import scheduler

from mailmind.bootstrap.mailbox import commands
from mailmind.bootstrap.mailbox import realtime
from mailmind.bootstrap.mailbox import store
from mailmind.bootstrap import assistant
from mailmind.mailbox.domain.events import Applied
from mailmind.mailbox.domain.events import Restored
from mailmind.mailbox.domain.events import FlagsChanged
from mailmind.mailbox.domain.events import InboxChanged


_wired = []                             # wire()'s subscriptions, each a function that ends it


def _after_send(_sent):
    """Compose's Sent or Failed: a queued send went out, or an attempt at one failed (with
    SSE live the page doesn't poll the outbox, so a send given up on would still look pending)."""
    mailbox.stats.invalidate()
    sse.publish("outbox", {})       # nudge the client to refresh the outbox view instantly


def _after_flags_changed(changed):
    """FlagsChanged: mail read or starred elsewhere shows on a list that's open."""
    sse.publish("flags-changed", {"account": changed.account, "folder": changed.folder, "count": changed.count})


def _after_draft_removed(gone):
    """Compose's DraftCopyRemoved: a send's draft left the server."""
    mailbox.folders.list_changed(gone.account, gone.folder)


def _after_idle(changed):
    """InboxChanged: IDLE saw these accounts' INBOX change."""
    mailbox.syncing.sync_guarded(set(changed.accounts))


def wire():
    """Subscribe the app to what the workers publish on mailmind.platform.bus: a send
    that went out or an attempt that failed, a queued move applied and one that won't be refresh the
    stats and tell the open tabs, and IDLE's
    news syncs those accounts. An account added, changed or removed brings IDLE's workers in line
    (realtime.reconcile, which does nothing until IDLE starts). Wiring again replaces the last
    wiring. Starts nothing."""
    unwire()
    _wired.extend([bus.subscribe(Sent, _after_send),
                   bus.subscribe(Failed, _after_send),
                   bus.subscribe(DraftCopyRemoved, _after_draft_removed),
                   bus.subscribe(Applied, mailbox.filing.settled),
                   bus.subscribe(Restored, mailbox.filing.settled),
                   bus.subscribe(InboxChanged, _after_idle),
                   bus.subscribe(FlagsChanged, _after_flags_changed)])
    _wired.extend(bus.subscribe(topic, realtime.reconcile) for topic in
                  (account_events.AccountAdded, account_events.AccountChanged, account_events.AccountRemoved))


def unwire():
    """End wire()'s subscriptions."""
    while _wired:
        _wired.pop()()


def _daemon(target):
    threading.Thread(target=target, name=getattr(target, "__name__", None), daemon=True).start()


def start(spawn=_daemon):
    """Everything a front door does before it serves: check the profile, create or migrate the
    schema, wire the workers, and start them: the two queues' workers, the background jobs on a
    scheduler (background.schedule says which), and IDLE. Read-only, only snooze wake starts, since it
    needs no mail server: sends and moves wait in their queues, and nothing syncs.

    `spawn(target)` runs each worker and each job's lane; by default on a daemon thread of its own."""
    profile.check()                      # a MAILMIND_PROFILE that names no profile stops the start
    store.init()
    wire()
    jobs = scheduler.Scheduler()
    background.schedule(jobs)
    background.JOBS = jobs                   # what /api/debug/jobs reports
    if background.nosync():
        jobs.start(spawn)
        print("(auto-sync disabled: the read-only profile writes nothing, so sends and moves wait "
              "in their queues; snooze still runs)", flush=True)
    else:
        spawn(compose.outbox.worker_loop)
        spawn(commands.worker_loop)
        jobs.start(spawn)
        realtime.start()                     # per-account IMAP IDLE workers + SSE dispatch
    print(f"[ai] {assistant.calls.describe_engine()}", flush=True)
    print(f"[imap] {mailbox.describe()}", flush=True)


def assess_batch() -> int:
    """`python -m mailmind assess --batch`: bring in a batch that has ended, and send what's waiting as the
    next. The app's assess job does the same for a backlog; this asks for any size. 0 when it could."""
    if not profile.writes_allowed():
        print("The read-only profile sends nothing out, so nothing was sent.")
        return 1
    store.init()
    if assistant.calls.engine() == "none":
        print("AI is off: there is no API key, so nothing was sent.")
        return 1
    assistant.triage.batch_collect(print)
    sent = assistant.triage.batch_submit(at_least=1, keep=0, log=print)
    status = assistant.triage.batch_status()
    if status["waiting"]:
        print(f"{status['waiting']} messages are out in a batch. Most end within the hour, and all within a day; "
              "the app brings the answers in when it next runs, or run this again.")
    elif not sent:
        print("Nothing was sent: no message is waiting, or today's AI limit has no room.")
    return 0
