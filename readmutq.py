"""Offline-safe read-mutation drain worker — a structural clone of outbox.py for read-side
mailbox mutations (trash/archive/junk/move/flag). When the route enqueues a mutation (because
IMAP was unreachable), this worker re-applies it once the server is back, exactly once.

Shares the outbox's machinery, a DurableQueue over the read_mutations table: a single worker
thread, atomic claim (queued→running), the `applied` flag as the exactly-once gate
(= outbox.delivered), exponential backoff, reset-stuck on boot. The ONE difference from the
outbox: the primary op here IS an IMAP op, so it runs as a job on the account's IMAP gate (the
outbox's SMTP send holds none).
"""
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import accounts
import store
import mailops
from mailmind.platform import bus, profile
from mailmind.platform.workers import gates
from mailmind.platform.workers.durable_queue import DurableQueue

MAX_ATTEMPTS = 5
_BACKOFF_BASE = 20      # secs; doubles per attempt
_BACKOFF_CAP = 900
_POLL = 30

QUEUE = DurableQueue("readmutq", "read_mutations", running="running", finished="done", finished_at="done_at",
                     gate="applied", connect=lambda: store.conn(), max_attempts=MAX_ATTEMPTS,
                     backoff_base=_BACKOFF_BASE, backoff_cap=_BACKOFF_CAP, poll=_POLL)


@dataclass(frozen=True)
class Applied:
    """A queued move or flag change reached the server and the local copy matches it (published
    on mailmind.platform.bus)."""
    mutation_id: int
    account: str


@dataclass(frozen=True)
class Restored:
    """A queued move won't happen (the server refused it, the queue gave up on it, or its account
    is gone), and its message is listed again (published on mailmind.platform.bus)."""
    mutation_id: int
    account: str


def _now():
    return int(time.time())


def kick():
    QUEUE.kick()


def _commit(fn):
    """Run fn(c) on a short-lived connection, commit, and return what fn returned."""
    c = store.conn()
    try:
        out = fn(c)
        c.commit()
        return out
    finally:
        c.close()


def _commit_get(mid):
    c = store.conn()
    try:
        return store.read_mut_get(c, mid)
    finally:
        c.close()


def _local_mirror(row):
    """Best-effort, idempotent: apply the same local DB change the inline path makes. A move-type
    op drops the rows it marked pending_removal when it was queued, or, when there are none (a
    sync stored the message again meanwhile, or it was queued before migration 23 and its row went
    then), the source folder's row by Message-ID. A flag op sets flags on the row by Message-ID.
    Re-running is harmless (delete of a gone row is a no-op; set_flags is idempotent)."""
    c = store.conn()
    try:
        if row["op"] == "flag":
            import json
            r = store.get_message_by_msgid(c, row["account"], row["message_id"]) if row["message_id"] else None
            if r is None:
                return
            flags = (json.loads(row["payload"] or "{}").get("flags") or {})
            store.set_local_flags(c, r["pk"], **{k: int(bool(v)) for k, v in flags.items()})
        else:
            pks = store.pending_removal_pks(c, row["id"])
            if not pks and row["message_id"]:
                r = c.execute("SELECT pk FROM messages WHERE account=? AND folder=? AND message_id=? "
                              "ORDER BY pk DESC LIMIT 1",
                              (row["account"], row["src_folder"], row["message_id"])).fetchone()
                pks = [r["pk"]] if r else []
            for pk in pks:
                store.delete_message(c, pk)
        c.commit()
    finally:
        c.close()


def _restore(c, row, error):
    """The queued move `row` won't happen: to error, and its message listed again."""
    QUEUE.give_up(c, row, error)
    store.restore_pending(c, row["id"])


def apply_one(mid):
    row = _commit(lambda c: QUEUE.claim(c, mid))
    if row is None:
        return                       # lost the claim (cancelled / another worker) — mirror outbox
    # exactly-once: a recovered row (applied=1, requeued by reset_stuck) skips the IMAP op and only
    # finishes bookkeeping, so a crash after the op can never re-move/re-expunge.
    if not row["applied"]:
        def apply():                                         # IMAP op takes the account's gate (unlike SMTP)
            fresh = _commit_get(mid)                          # re-read under the gate: an undo may have
            if fresh is None:                                #   cancelled (deleted) the row between claim and here
                return "cancelled", None
            try:
                accounts.get(fresh["account"])
            except KeyError:
                return "removed", "account removed"
            why = mailops.apply_queued_mutation(fresh)
            return ("refused", why) if why else ("applied", None)
        try:
            outcome, why = gates.run(row["account"], apply, priority=gates.Priority.AFTER_USER)
            if outcome in ("removed", "refused"):            # no retry can make it happen
                _commit(lambda c: _restore(c, row, why))
                bus.publish(Restored(mid, row["account"]))
                print(f"[readmutq] #{mid} won't apply ({why}); its message is listed again", flush=True)
            if outcome != "applied":
                return
        except mailops._UNREACHABLE as e:
            err = type(e).__name__

            def later(c):
                terminal, delay = QUEUE.retry_later(c, row, err, _now())
                if terminal:                                 # given up: the message is still where it was
                    store.restore_pending(c, mid)
                return terminal, delay
            terminal, delay = _commit(later)
            if terminal:
                bus.publish(Restored(mid, row["account"]))
            print(f"[readmutq] #{mid} unreachable ({err}); "
                  f"{'giving up' if terminal else f'retry {delay}s'}", flush=True)
            return
        _commit(lambda c: QUEUE.mark_gate(c, mid))   # applied=1, committed BEFORE the local mirror
    try:
        _local_mirror(row)
        _commit(lambda c: QUEUE.finish(c, mid, datetime.now(timezone.utc).isoformat()))
        bus.publish(Applied(mid, row["account"]))
        print(f"[readmutq] applied #{mid} {row['op']}", flush=True)
    except Exception as e:
        print(f"[readmutq] post-apply bookkeeping for #{mid} deferred: {type(e).__name__}", flush=True)


def drain():
    if not profile.writes_allowed():     # read-only: moves wait here for a full run (D4)
        return
    QUEUE.drain(apply_one, _now())       # one bad row is logged and never aborts the rest


def worker_loop():
    QUEUE.run_forever(drain)             # recovers rows a crash left running, then drains
