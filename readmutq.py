"""Offline-safe read-mutation drain worker — a structural clone of Compose's outbox for read-side
mailbox mutations (trash/archive/junk/move/flag). When the route enqueues a mutation (because
IMAP was unreachable), this worker re-applies it once the server is back, exactly once.

Shares the outbox's machinery, a DurableQueue over the read_mutations table: a single worker
thread, atomic claim (queued→running), the `applied` flag as the exactly-once gate
(= outbox.delivered), exponential backoff, reset-stuck on boot. The ONE difference from the
outbox: the primary op here IS an IMAP op, so it runs as a job on the account's IMAP gate (the
outbox's SMTP send holds none).
"""
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from mailmind.bootstrap import accounts
from mailmind.bootstrap.mailbox import store
from mailmind.bootstrap.mailbox import commands
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
    """A queued move or flag change won't happen (the server refused it, the queue gave up on it,
    or its account is gone), and the local row is as the server has it again: a move's message
    listed, a flag change's flags put back (published on mailmind.platform.bus)."""
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
            r = _flagged_row(c, row)
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


def _flagged_row(c, row):
    """The local row a queued flag change is for: the one in the folder it was asked in, else the
    newest with its Message-ID, else the row it was asked on."""
    if row["message_id"]:
        r = c.execute("SELECT pk FROM messages WHERE account=? AND folder=? AND message_id=? "
                      "ORDER BY pk DESC LIMIT 1", (row["account"], row["src_folder"], row["message_id"])).fetchone()
        return r or store.get_message_by_msgid(c, row["account"], row["message_id"])
    pk = json.loads(row["payload"] or "{}").get("pk")
    return store.get_message(c, pk) if pk else None


def _give_back(c, row):
    """The queued change `row` won't happen: a move's message is listed again, and a flag change's
    row gets back the flags it had when the change was asked for."""
    store.restore_pending(c, row["id"])
    if row["op"] == "flag":
        r = _flagged_row(c, row)
        was = json.loads(row["payload"] or "{}").get("was") or {}
        if r is not None and was:
            store.set_local_flags(c, r["pk"], **{k: int(bool(v)) for k, v in was.items()})


def _restore(c, row, error):
    """The queued change `row` won't happen: to error, and the local row as the server has it."""
    QUEUE.give_up(c, row, error)
    _give_back(c, row)


def _take_up(c, row):
    """The given-up change `row` is asked for again: as when it was queued, a move's message leaves
    the lists and a flag change shows, its `was` now the flags it replaces this time."""
    payload = json.loads(row["payload"] or "{}")
    if row["op"] == "flag":
        r = _flagged_row(c, row)
        r = store.get_message(c, r["pk"]) if r is not None else None
        if r is None:
            return
        flags = payload.get("flags") or {}
        payload["was"] = {k: bool(r[k]) for k in flags}
        store.read_mut_set_payload(c, row["id"], json.dumps(payload))
        store.set_local_flags(c, r["pk"], **{k: int(bool(v)) for k, v in flags.items()})
        return
    if row["message_id"]:
        r = c.execute("SELECT pk FROM messages WHERE account=? AND folder=? AND message_id=? "
                      "ORDER BY pk DESC LIMIT 1", (row["account"], row["src_folder"], row["message_id"])).fetchone()
    else:
        r = store.get_message(c, payload.get("pk")) if payload.get("pk") else None
    if r is not None:
        store.mark_pending_removal(c, r["pk"], row["id"])


def list_open(limit=100):
    """What the pending-changes tray shows: the changes that haven't reached a server, and how many
    wait and how many were given up on. A subject the row doesn't hold comes from its message."""
    c = store.conn()
    try:
        items = []
        for r in store.read_mut_open(c, limit):
            payload = json.loads(r["payload"] or "{}")
            subject = payload.get("subject")
            if subject is None:
                found = _flagged_row(c, r)
                found = store.get_message(c, found["pk"]) if found is not None else None
                subject = found["subject"] if found is not None else ""
            items.append({"id": r["id"], "account": r["account"], "op": r["op"], "folder": r["src_folder"],
                          "dest": r["dest"], "flags": payload.get("flags"), "state": r["state"],
                          "attempts": r["attempts"], "error": r["last_error"], "subject": subject,
                          "next_attempt_at": r["next_attempt_at"], "created_at": r["created_at"]})
        counts = store.read_mut_open_counts(c)
    finally:
        c.close()
    return {"items": items, "waiting": counts.get("queued", 0) + counts.get("running", 0),
            "stuck": counts.get("error", 0)}


def retry(mid):
    """Ask again for a change the queue gave up on. False when it isn't one."""
    def go(c):
        row = store.read_mut_get(c, mid)
        if row is None or not QUEUE.requeue(c, mid):
            return False
        _take_up(c, row)
        return True
    ok = _commit(go)
    if ok:
        kick()
    return ok


def discard(mid):
    """Drop a change that hasn't reached a server: its message is as the server has it again, and
    there is nothing for Undo to reverse. Returns the row, or None when it had reached one or is gone.
    The caller holds the account's IMAP gate, so a try at it is over or hasn't begun."""
    def go(c):
        row = store.read_mut_get(c, mid)
        if row is None or not QUEUE.cancel_unapplied(c, mid):
            return None
        _give_back(c, row)
        if row["op"] != "flag" and row["message_id"]:
            store.undo_forget_move(c, row["account"], row["message_id"], row["src_folder"])
        return row
    return _commit(go)


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
            why = commands.apply_queued_mutation(fresh)
            if why:
                return "refused", why
            _commit(lambda c: QUEUE.mark_gate(c, mid))       # applied=1, before the gate lets a cancel in
            return "applied", None
        try:
            outcome, why = gates.run(row["account"], apply, priority=gates.Priority.AFTER_USER)
            if outcome in ("removed", "refused"):            # no retry can make it happen
                _commit(lambda c: _restore(c, row, why))
                bus.publish(Restored(mid, row["account"]))
                print(f"[readmutq] #{mid} won't apply ({why}); its message is listed again", flush=True)
            if outcome != "applied":
                return
        except commands.UNREACHABLE as e:
            err = type(e).__name__

            def later(c):
                terminal, delay = QUEUE.retry_later(c, row, err, _now())
                if terminal:                                 # given up: the message is still as it was
                    _give_back(c, row)
                return terminal, delay
            terminal, delay = _commit(later)
            if terminal:
                bus.publish(Restored(mid, row["account"]))
            print(f"[readmutq] #{mid} unreachable ({err}); "
                  f"{'giving up' if terminal else f'retry {delay}s'}", flush=True)
            return
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
