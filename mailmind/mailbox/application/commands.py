"""One path for a change to the mail on a server, online or offline.

Accept, in one transaction: the command's row, its message out of the lists (or its flags shown) and what Undo will
need. Submit: a try on the account's gate, which a click waits for only so long. Settle, under the same gate: `applied`,
what the server's answer left and the local row commit together. A connection failure leaves the row for the worker,
which backs off and tries again, and a try that followed another asks the server first (ServerOps.apply), so a change
applies at most once.

The queue is a DurableQueue over the mail_commands table, as Compose's outbox is over its own: an atomic claim, the
`applied` flag as the exactly-once gate, backoff, and running rows reset at boot. The one difference from the outbox:
the step here is an IMAP op, so it runs as a job on the account's IMAP gate.
"""
import json
import secrets
import time
from datetime import datetime, timezone

from mailmind.kernel.when import now_iso
from mailmind.mailbox.application.ports import Accounts, StoredMail
from mailmind.mailbox.application.server_ops import UNREACHABLE, ServerOps
from mailmind.mailbox.domain import command
from mailmind.mailbox.domain.events import Applied, Restored
from mailmind.platform import bus, profile
from mailmind.platform.workers import gates
from mailmind.platform.workers.durable_queue import DurableQueue

_UNDO_LABEL = {"trash": "Trashed", "archive": "Archived", "junk": "Marked junk", "move": "Moved"}


def _now():
    return int(time.time())


def _request_id(mutation_id):
    """The client's mutation_id names one request, so a resubmit of it dedupes; without one, every request is its own.
    A key built from the message alone made a second trash of the same message, after the first had been applied and
    the message moved back, answer "queued" and do nothing."""
    if isinstance(mutation_id, str) and 0 < len(mutation_id) <= 128:
        return mutation_id
    return secrets.token_hex(16)


class MailCommands:
    UNREACHABLE = UNREACHABLE
    MAX_ATTEMPTS = 101      # about a day of tries, as the backoff goes: an outage isn't a reason to put mail back
    FILE_WAIT = 2.5         # seconds a click waits for the account's gate before the worker takes it over
    _BACKOFF_BASE = 20      # secs; doubles per attempt
    _BACKOFF_CAP = 900
    _POLL = 30
    gates = gates

    def __init__(self, store: StoredMail, accounts: Accounts, server: ServerOps):
        self._store = store
        self._accounts = accounts
        self._server = server
        self.QUEUE = DurableQueue("commands", "mail_commands", running="running", finished="done", finished_at="done_at",
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


    # ── accept ──────────────────────────────────────────────────────────────────────────────────

    def _claim(self, c, id_):
        """The new command's row, claimed for a try now; None when this process only reads, and only a full run's
        worker will try it."""
        if not profile.writes_allowed():
            return None
        self._store.command_mark_begun(c, id_)
        return self.QUEUE.claim(c, id_)


    def accept_move(self, kind, pk, dest, mutation_id, push_undo=None):
        """Accept a move of message `pk`: an Accepted, or None when there is no such message, or it is on its way out
        already. `push_undo(c, entry)`, in the same transaction, keeps what reverses it; without one, nothing will."""
        c = self._store.conn()
        try:
            row = self._store.get_message(c, pk)
            if not row or row["pending_removal"] is not None:
                return None
            email, mid = row["account"], (row["message_id"] or "").strip()
            try:
                acct = self._accounts.get(email)
                by_label = self._accounts.profile(acct).archive_is_label_removal
                if kind == "trash":                  # known with no server: kept for an Undo before it lands
                    dest = self._accounts.trash_folder(acct)
            except KeyError:                         # the account is gone, which a try finds out
                by_label = False
            params = {"pk": pk, "uid": row["uid"], "epoch": self._store.get_uidvalidity(c, email, row["folder"]),
                      "seen": bool(row["seen"]), "subject": row["subject"], "thread_id": row["thread_id"]}
            id_, created = self._store.command_insert(c, {
                "account": email, "idempotency_key": f"{kind}:{email}:{mid or pk}:{_request_id(mutation_id)}", "kind": kind,
                "message_id": mid, "src_folder": row["folder"], "dest": dest, "params": json.dumps(params),
                "next_attempt_at": 0, "created_at": now_iso()})
            if not created:                          # a resubmit of the same request
                return command.Accepted(id_, email, False, False)
            self._store.mark_pending_removal(c, pk, id_)
            undoable = bool(mid) and push_undo is not None
            if mid and push_undo is not None:
                push_undo(c, {"kind": "gmail_archive" if (kind == "archive" and by_label) else "move", "command": id_,
                              "account": email, "src_folder": row["folder"], "dest_folder": dest or "",
                              "message_id": mid, "label": _UNDO_LABEL.get(kind, "Updated"),
                              "subject": row["subject"], "thread_id": row["thread_id"]})
            claimed = self._claim(c, id_)
            c.commit()
            return command.Accepted(id_, email, True, undoable, claimed)
        finally:
            c.close()


    def accept_flags(self, pk, flags, mutation_id):
        """Accept a flag change on message `pk`, which shows on its row at once: an Accepted, or None when there is
        no such message."""
        c = self._store.conn()
        try:
            row = self._store.get_message(c, pk)
            if not row:
                return None
            id_, created = self._store.flag_command(c, row, flags, _request_id(mutation_id), now_iso())
            if not created:
                return command.Accepted(id_, row["account"], False, False)
            claimed = self._claim(c, id_)
            c.commit()
            return command.Accepted(id_, row["account"], True, False, claimed)
        finally:
            c.close()


    # ── a try, and what settles it ──────────────────────────────────────────────────────────────

    def _local_mirror(self, c, row):
        """Idempotent: what the server now holds, on the local rows. A move drops the rows it marked pending_removal
        or, when there are none (a sync stored the message again meanwhile, or it was queued before migration 23 and
        its row went then), the source folder's row by Message-ID. A flag change sets the flags on the row by
        Message-ID. Re-running is harmless (delete of a gone row is a no-op; set_flags is idempotent)."""
        if row["kind"] == "flag":
            r = self._flagged_row(c, row)
            if r is None:
                return
            flags = (json.loads(row["params"] or "{}").get("flags") or {})
            self._store.set_local_flags(c, r["pk"], **{k: int(bool(v)) for k, v in flags.items()})
            return
        pks = self._store.pending_removal_pks(c, row["id"])
        if not pks and row["message_id"]:
            r = c.execute("SELECT pk FROM messages WHERE account=? AND folder=? AND message_id=? "
                          "ORDER BY pk DESC LIMIT 1",
                          (row["account"], row["src_folder"], row["message_id"])).fetchone()
            pks = [r["pk"]] if r else []
        for pk in pks:
            self._store.delete_message(c, pk)


    def _settle(self, row, answer):
        """The server holds the change: `applied`, what its answer left and the local rows, in one transaction, while
        the gate still keeps out a cancel."""
        def go(c):
            self.QUEUE.mark_gate(c, row["id"])
            self._store.command_record(c, row["id"], answer.dest,
                                       json.dumps({"unseen": True}) if answer.unseen else None)
            self._local_mirror(c, row)
            self.QUEUE.finish(c, row["id"], datetime.now(timezone.utc).isoformat())
        self._commit(go)


    def _try_on_gate(self, mid, just_accepted):
        """One try, as a job on the account's gate: applied, cancelled or one of WONT_HAPPEN. The row is read again here,
        since an Undo or a Discard may have deleted it while this waited. A row with `applied` set (a crash's, or
        one from before settling took one transaction) has only its bookkeeping left."""
        fresh = self._commit_get(mid)
        if fresh is None:
            return command.CANCELLED
        answer = command.ServerAnswer()
        if not fresh["applied"]:
            try:
                self._accounts.get(fresh["account"])
            except KeyError:
                return command.REMOVED
            first_try = just_accepted or not fresh["begun"]
            if not fresh["begun"]:
                self._commit(lambda c: self._store.command_mark_begun(c, mid))
            answer = self._server.apply(fresh, first_try)
            if answer.won_t:
                return answer.won_t
        self._settle(fresh, answer)
        return command.APPLIED


    def submit(self, accepted):
        """A try now, for the person waiting: what became of the command. It waits for the gate only FILE_WAIT seconds,
        and a server that can't be reached leaves the command for the worker. A change that won't happen leaves nothing
        behind, since whoever is waiting hears of it now: the message is as it was, and the tray stays clear."""
        row = accepted.claimed
        if row is None:
            return command.QUEUED
        mid = row["id"]
        try:
            outcome = self.gates.run(row["account"], lambda: self._try_on_gate(mid, True),
                                     priority=self.gates.Priority.INTERACTIVE, deadline=self.FILE_WAIT)
        except self.gates.Busy:              # it never ran
            self._commit(lambda c: (self.QUEUE.put_back(c, mid), self._store.command_never_begun(c, mid)))
            self.kick()
            return command.WAITING
        except UNREACHABLE:
            self._commit(lambda c: self.QUEUE.put_back(c, mid))
            self.kick()
            return command.QUEUED
        except Exception:                    # no answer to give, and a hidden message mustn't wait for a restart
            self._commit(lambda c: self.QUEUE.put_back(c, mid))
            self.kick()
            raise
        if outcome in command.WONT_HAPPEN:
            self._commit(lambda c: self._forget(c, row))
        return outcome


    def _forget(self, c, row):
        """The change someone is waiting on won't happen, and they hear so now: as if it was never asked."""
        self._give_back(c, row)
        if row["kind"] != "flag" and row["message_id"]:
            self._store.undo_forget_move(c, row["account"], row["message_id"], row["src_folder"])
        self.QUEUE.cancel_unapplied(c, row["id"])


    def _flagged_row(self, c, row):
        """The local row a flag change is for: the one in the folder it was asked in, else the newest with its
        Message-ID, else the row it was asked on."""
        if row["message_id"]:
            r = c.execute("SELECT pk FROM messages WHERE account=? AND folder=? AND message_id=? "
                          "ORDER BY pk DESC LIMIT 1", (row["account"], row["src_folder"], row["message_id"])).fetchone()
            return r or self._store.get_message_by_msgid(c, row["account"], row["message_id"])
        pk = json.loads(row["params"] or "{}").get("pk")
        return self._store.get_message(c, pk) if pk else None


    def _give_back(self, c, row):
        """The change `row` won't happen: a move's message is listed again, and a flag change's row gets back the flags
        it had when the change was asked for."""
        self._store.restore_pending(c, row["id"])
        if row["kind"] == "flag":
            r = self._flagged_row(c, row)
            was = json.loads(row["params"] or "{}").get("was") or {}
            if r is not None and was:
                self._store.set_local_flags(c, r["pk"], **{k: int(bool(v)) for k, v in was.items()})


    def _restore(self, c, row, error):
        """The change `row` won't happen, and no one is waiting to hear: to error, for the tray, and the local row as
        the server has it."""
        self.QUEUE.give_up(c, row, error)
        self._give_back(c, row)


    def _take_up(self, c, row):
        """The given-up change `row` is asked for again: as when it was accepted, a move's message leaves the lists and
        a flag change shows, its `was` now the flags it replaces this time."""
        params = json.loads(row["params"] or "{}")
        if row["kind"] == "flag":
            r = self._flagged_row(c, row)
            r = self._store.get_message(c, r["pk"]) if r is not None else None
            if r is None:
                return
            flags = params.get("flags") or {}
            params["was"] = {k: bool(r[k]) for k in flags}
            self._store.command_set_params(c, row["id"], json.dumps(params))
            self._store.set_local_flags(c, r["pk"], **{k: int(bool(v)) for k, v in flags.items()})
            return
        if row["message_id"]:
            r = c.execute("SELECT pk FROM messages WHERE account=? AND folder=? AND message_id=? "
                          "ORDER BY pk DESC LIMIT 1", (row["account"], row["src_folder"], row["message_id"])).fetchone()
        else:
            r = self._store.get_message(c, params.get("pk")) if params.get("pk") else None
        if r is not None:
            self._store.mark_pending_removal(c, r["pk"], row["id"])


    # ── the tray ────────────────────────────────────────────────────────────────────────────────

    def list_open(self, limit=100):
        """What the pending-changes tray shows: the changes that haven't reached a server, and how many
        wait and how many were given up on. A subject the row doesn't hold comes from its message."""
        c = self._store.conn()
        try:
            items = []
            for r in self._store.command_open(c, limit):
                params = json.loads(r["params"] or "{}")
                subject = params.get("subject")
                if subject is None:
                    found = self._flagged_row(c, r)
                    found = self._store.get_message(c, found["pk"]) if found is not None else None
                    subject = found["subject"] if found is not None else ""
                items.append({"id": r["id"], "account": r["account"], "op": r["kind"], "folder": r["src_folder"],
                              "dest": r["dest"], "flags": params.get("flags"), "state": r["state"],
                              "attempts": r["attempts"], "error": r["last_error"], "subject": subject,
                              "next_attempt_at": r["next_attempt_at"], "created_at": r["created_at"]})
            counts = self._store.command_open_counts(c)
        finally:
            c.close()
        return {"items": items, "waiting": counts.get("queued", 0) + counts.get("running", 0),
                "stuck": counts.get("error", 0)}


    def retry(self, mid):
        """Ask again for a change given up on. False when it isn't one."""
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


    # ── the worker ──────────────────────────────────────────────────────────────────────────────

    def apply_one(self, mid):
        row = self._commit(lambda c: self.QUEUE.claim(c, mid))
        if row is None:
            return                       # lost the claim (cancelled / another worker) — mirror outbox
        try:
            outcome = self.gates.run(row["account"], lambda: self._try_on_gate(mid, False),
                                     priority=self.gates.Priority.AFTER_USER)
        except Exception as e:           # a connection failure or anything else: a hidden message can't wait for a restart
            err = type(e).__name__

            def later(c):
                terminal, delay = self.QUEUE.retry_later(c, row, err, _now())
                if terminal:                                 # given up: the message is still as it was
                    self._give_back(c, row)
                return terminal, delay
            terminal, delay = self._commit(later)
            if terminal:
                bus.publish(Restored(mid, row["account"]))
            print(f"[commands] #{mid} unreachable ({err}); "
                  f"{'giving up' if terminal else f'retry {delay}s'}", flush=True)
            return
        if outcome in command.WONT_HAPPEN:               # no retry can make it happen
            why = "account removed" if outcome == command.REMOVED else outcome
            self._commit(lambda c: self._restore(c, row, why))
            bus.publish(Restored(mid, row["account"]))
            print(f"[commands] #{mid} won't apply ({why}); its message is listed again", flush=True)
        elif outcome == command.APPLIED:
            bus.publish(Applied(mid, row["account"]))
            print(f"[commands] applied #{mid} {row['kind']}", flush=True)


    def drain(self):
        if not profile.writes_allowed():     # read-only: moves wait here for a full run (D4)
            return
        self.QUEUE.drain(self.apply_one, _now())       # one bad row is logged and never aborts the rest


    def worker_loop(self):
        self.QUEUE.run_forever(self.drain)             # recovers rows a crash left running, then drains
