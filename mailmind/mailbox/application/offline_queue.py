"""What couldn't reach a server, applied once it can be: moves and flag changes queued because IMAP was unreachable.

The same machinery as Compose's outbox, a DurableQueue over the mail_commands table: a single worker thread, an atomic
claim (queued to running), the `applied` flag as the exactly-once gate, exponential backoff, and running rows reset at
boot. The one difference from the outbox: the primary op here is an IMAP op, so it runs as a job on the account's IMAP
gate (the outbox's SMTP send holds none).
"""
import json
import time
from datetime import datetime, timezone

from mailmind.mailbox.application.commands import UNREACHABLE, MailCommands
from mailmind.mailbox.application.ports import Accounts, StoredMail
from mailmind.mailbox.domain.events import Applied, Restored
from mailmind.platform import bus, profile
from mailmind.platform.workers import gates
from mailmind.platform.workers.durable_queue import DurableQueue


def _now():
    return int(time.time())


class OfflineQueue:
    MAX_ATTEMPTS = 5
    _BACKOFF_BASE = 20      # secs; doubles per attempt
    _BACKOFF_CAP = 900
    _POLL = 30
    gates = gates

    def __init__(self, store: StoredMail, accounts: Accounts, commands: MailCommands):
        self._store = store
        self._accounts = accounts
        self._commands = commands
        self.QUEUE = DurableQueue("readmutq", "mail_commands", running="running", finished="done", finished_at="done_at",
                                  gate="applied", connect=lambda: store.conn(), max_attempts=self.MAX_ATTEMPTS,
                                  backoff_base=self._BACKOFF_BASE, backoff_cap=self._BACKOFF_CAP, poll=self._POLL)

    def kick(self):
        self.QUEUE.kick()


    def _commit(self, fn):
        """Run fn(c) on a short-lived connection, commit, and return what fn returned."""
        c = self._store.conn()
        try:
            out = fn(c)
            c.commit()
            return out
        finally:
            c.close()


    def _commit_get(self, mid):
        c = self._store.conn()
        try:
            return self._store.command_get(c, mid)
        finally:
            c.close()


    def _local_mirror(self, row):
        """Best-effort, idempotent: apply the same local DB change the inline path makes. A move-type
        op drops the rows it marked pending_removal when it was queued, or, when there are none (a
        sync stored the message again meanwhile, or it was queued before migration 23 and its row went
        then), the source folder's row by Message-ID. A flag op sets flags on the row by Message-ID.
        Re-running is harmless (delete of a gone row is a no-op; set_flags is idempotent)."""
        c = self._store.conn()
        try:
            if row["kind"] == "flag":
                r = self._flagged_row(c, row)
                if r is None:
                    return
                flags = (json.loads(row["params"] or "{}").get("flags") or {})
                self._store.set_local_flags(c, r["pk"], **{k: int(bool(v)) for k, v in flags.items()})
            else:
                pks = self._store.pending_removal_pks(c, row["id"])
                if not pks and row["message_id"]:
                    r = c.execute("SELECT pk FROM messages WHERE account=? AND folder=? AND message_id=? "
                                  "ORDER BY pk DESC LIMIT 1",
                                  (row["account"], row["src_folder"], row["message_id"])).fetchone()
                    pks = [r["pk"]] if r else []
                for pk in pks:
                    self._store.delete_message(c, pk)
            c.commit()
        finally:
            c.close()


    def _flagged_row(self, c, row):
        """The local row a queued flag change is for: the one in the folder it was asked in, else the
        newest with its Message-ID, else the row it was asked on."""
        if row["message_id"]:
            r = c.execute("SELECT pk FROM messages WHERE account=? AND folder=? AND message_id=? "
                          "ORDER BY pk DESC LIMIT 1", (row["account"], row["src_folder"], row["message_id"])).fetchone()
            return r or self._store.get_message_by_msgid(c, row["account"], row["message_id"])
        pk = json.loads(row["params"] or "{}").get("pk")
        return self._store.get_message(c, pk) if pk else None


    def _give_back(self, c, row):
        """The queued change `row` won't happen: a move's message is listed again, and a flag change's
        row gets back the flags it had when the change was asked for."""
        self._store.restore_pending(c, row["id"])
        if row["kind"] == "flag":
            r = self._flagged_row(c, row)
            was = json.loads(row["params"] or "{}").get("was") or {}
            if r is not None and was:
                self._store.set_local_flags(c, r["pk"], **{k: int(bool(v)) for k, v in was.items()})


    def _restore(self, c, row, error):
        """The queued change `row` won't happen: to error, and the local row as the server has it."""
        self.QUEUE.give_up(c, row, error)
        self._give_back(c, row)


    def _take_up(self, c, row):
        """The given-up change `row` is asked for again: as when it was queued, a move's message leaves
        the lists and a flag change shows, its `was` now the flags it replaces this time."""
        payload = json.loads(row["params"] or "{}")
        if row["kind"] == "flag":
            r = self._flagged_row(c, row)
            r = self._store.get_message(c, r["pk"]) if r is not None else None
            if r is None:
                return
            flags = payload.get("flags") or {}
            payload["was"] = {k: bool(r[k]) for k in flags}
            self._store.command_set_params(c, row["id"], json.dumps(payload))
            self._store.set_local_flags(c, r["pk"], **{k: int(bool(v)) for k, v in flags.items()})
            return
        if row["message_id"]:
            r = c.execute("SELECT pk FROM messages WHERE account=? AND folder=? AND message_id=? "
                          "ORDER BY pk DESC LIMIT 1", (row["account"], row["src_folder"], row["message_id"])).fetchone()
        else:
            r = self._store.get_message(c, payload.get("pk")) if payload.get("pk") else None
        if r is not None:
            self._store.mark_pending_removal(c, r["pk"], row["id"])


    def list_open(self, limit=100):
        """What the pending-changes tray shows: the changes that haven't reached a server, and how many
        wait and how many were given up on. A subject the row doesn't hold comes from its message."""
        c = self._store.conn()
        try:
            items = []
            for r in self._store.command_open(c, limit):
                payload = json.loads(r["params"] or "{}")
                subject = payload.get("subject")
                if subject is None:
                    found = self._flagged_row(c, r)
                    found = self._store.get_message(c, found["pk"]) if found is not None else None
                    subject = found["subject"] if found is not None else ""
                items.append({"id": r["id"], "account": r["account"], "op": r["kind"], "folder": r["src_folder"],
                              "dest": r["dest"], "flags": payload.get("flags"), "state": r["state"],
                              "attempts": r["attempts"], "error": r["last_error"], "subject": subject,
                              "next_attempt_at": r["next_attempt_at"], "created_at": r["created_at"]})
            counts = self._store.command_open_counts(c)
        finally:
            c.close()
        return {"items": items, "waiting": counts.get("queued", 0) + counts.get("running", 0),
                "stuck": counts.get("error", 0)}


    def retry(self, mid):
        """Ask again for a change the queue gave up on. False when it isn't one."""
        def go(c):
            row = self._store.command_get(c, mid)
            if row is None or not self.QUEUE.requeue(c, mid):
                return False
            self._take_up(c, row)
            return True
        ok = self._commit(go)
        if ok:
            self.kick()
        return ok


    def discard(self, mid):
        """Drop a change that hasn't reached a server: its message is as the server has it again, and
        there is nothing for Undo to reverse. Returns the row, or None when it had reached one or is gone.
        The caller holds the account's IMAP gate, so a try at it is over or hasn't begun."""
        def go(c):
            row = self._store.command_get(c, mid)
            if row is None or not self.QUEUE.cancel_unapplied(c, mid):
                return None
            self._give_back(c, row)
            if row["kind"] != "flag" and row["message_id"]:
                self._store.undo_forget_move(c, row["account"], row["message_id"], row["src_folder"])
            return row
        return self._commit(go)


    def apply_one(self, mid):
        row = self._commit(lambda c: self.QUEUE.claim(c, mid))
        if row is None:
            return                       # lost the claim (cancelled / another worker) — mirror outbox
        # exactly-once: a recovered row (applied=1, requeued by reset_stuck) skips the IMAP op and only
        # finishes bookkeeping, so a crash after the op can never re-move/re-expunge.
        if not row["applied"]:
            def apply():                                         # IMAP op takes the account's gate (unlike SMTP)
                fresh = self._commit_get(mid)                          # re-read under the gate: an undo may have
                if fresh is None:                                #   cancelled (deleted) the row between claim and here
                    return "cancelled", None
                try:
                    self._accounts.get(fresh["account"])
                except KeyError:
                    return "removed", "account removed"
                why = self._commands.apply_queued_mutation(fresh)
                if why:
                    return "refused", why
                self._commit(lambda c: self.QUEUE.mark_gate(c, mid))       # applied=1, before the gate lets a cancel in
                return "applied", None
            try:
                outcome, why = self.gates.run(row["account"], apply, priority=self.gates.Priority.AFTER_USER)
                if outcome in ("removed", "refused"):            # no retry can make it happen
                    self._commit(lambda c: self._restore(c, row, why))
                    bus.publish(Restored(mid, row["account"]))
                    print(f"[readmutq] #{mid} won't apply ({why}); its message is listed again", flush=True)
                if outcome != "applied":
                    return
            except UNREACHABLE as e:
                err = type(e).__name__

                def later(c):
                    terminal, delay = self.QUEUE.retry_later(c, row, err, _now())
                    if terminal:                                 # given up: the message is still as it was
                        self._give_back(c, row)
                    return terminal, delay
                terminal, delay = self._commit(later)
                if terminal:
                    bus.publish(Restored(mid, row["account"]))
                print(f"[readmutq] #{mid} unreachable ({err}); "
                      f"{'giving up' if terminal else f'retry {delay}s'}", flush=True)
                return
        try:
            self._local_mirror(row)
            self._commit(lambda c: self.QUEUE.finish(c, mid, datetime.now(timezone.utc).isoformat()))
            bus.publish(Applied(mid, row["account"]))
            print(f"[readmutq] applied #{mid} {row['op']}", flush=True)
        except Exception as e:
            print(f"[readmutq] post-apply bookkeeping for #{mid} deferred: {type(e).__name__}", flush=True)


    def drain(self):
        if not profile.writes_allowed():     # read-only: moves wait here for a full run (D4)
            return
        self.QUEUE.drain(self.apply_one, _now())       # one bad row is logged and never aborts the rest


    def worker_loop(self):
        self.QUEUE.run_forever(self.drain)             # recovers rows a crash left running, then drains
