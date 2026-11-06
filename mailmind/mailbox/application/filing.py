"""Filing a message as a person asks: on the account's gate, reversible by Undo, and queued when the server can't be
reached. The tray's Retry and Discard of what is queued are here too."""
import json
import secrets

from mailmind.kernel.when import now_iso
from mailmind.mailbox.application.commands import MailCommands
from mailmind.mailbox.application.server_ops import UNREACHABLE, ServerOps
from mailmind.mailbox.application.folders import Folders
from mailmind.mailbox.application.ports import Accounts, Attention, StoredMail
from mailmind.mailbox.application.stats import LiveStats
from mailmind.mailbox.application.sync import Ingest
from mailmind.mailbox.application.undo import Undo
from mailmind.platform import sse
from mailmind.platform.workers import gates

_UNDO_LABEL = {"trash": "Trashed", "archive": "Archived", "junk": "Marked junk", "move": "Moved"}


class Filing:
    PENDING_WAIT = 15       # seconds the tray's Discard waits for a try at the change to end

    def __init__(self, store: StoredMail, accounts: Accounts, server: ServerOps, commands: MailCommands, ingest: Ingest,
                 undo: Undo, stats: LiveStats, folders: Folders, attention: Attention):
        self._store = store
        self._accounts = accounts
        self._server = server
        self._commands = commands
        self._ingest = ingest
        self._undo = undo
        self._stats = stats
        self._folders = folders
        self._attention = attention

    def _readmut_enqueue(self, op, pk, dest, undo_group, mutation_id):
        """IMAP was unreachable — durably queue this move-type mutation, take the message out of the
        lists (its row stays, marked pending_removal, until the move settles), push the undo
        descriptor, and report it queued. The offline queue re-applies it (resolving dest online) once the
        server is reachable again, and lists the message again if the move doesn't happen."""
        c = self._store.conn()
        row = self._store.get_message(c, pk)
        if not row:
            c.close()
            return {"ok": False, "msg": "not found"}
        acct, mid = row["account"], (row["message_id"] or "").strip()
        try:
            is_gmail = self._accounts.profile(self._accounts.get(acct)).archive_is_label_removal   # archive drops a label
        except KeyError:
            is_gmail = False
        if op == "trash" and not dest:                  # trash dest is static (no IMAP) — store it for undo
            try:
                dest = self._accounts.trash_folder(self._accounts.get(acct))
            except Exception:
                dest = None
        payload = {"pk": pk, "uid": row["uid"], "is_gmail": is_gmail,
                   "subject": row["subject"], "thread_id": row["thread_id"]}
        # The client's mutation_id names one request, so a resubmit of it dedupes; without one, every
        # request is its own. A key built from the message alone made a second trash of the same message,
        # after the first had been applied and the message moved back, answer "queued" and do nothing.
        if not (isinstance(mutation_id, str) and 0 < len(mutation_id) <= 128):
            mutation_id = secrets.token_hex(16)
        idem = f"{op}:{acct}:{mid or pk}:{mutation_id}"
        _id, created = self._store.command_insert(c, {"account": acct, "idempotency_key": idem, "kind": op,
                                  "message_id": mid, "src_folder": row["folder"], "dest": dest,
                                  "params": json.dumps(payload), "next_attempt_at": 0,
                                  "created_at": now_iso()})
        if not created:                             # duplicate submit (same mutation_id) — already queued
            c.close()
            return {"ok": True, "msg": "queued", "status": "queued", "undoable": False}
        undo = None
        if mid:
            undo = {"kind": "gmail_archive" if (op == "archive" and is_gmail) else "move",
                    "account": acct, "src_folder": row["folder"], "dest_folder": dest or "",
                    "message_id": mid, "label": _UNDO_LABEL.get(op, "Updated"),
                    "subject": row["subject"], "thread_id": row["thread_id"]}
        self._store.mark_pending_removal(c, pk, _id)      # out of the lists, as the online path's move takes it
        c.commit()
        c.close()
        if undo:
            self._undo.push(undo, undo_group)
        self._commands.kick()                             # drain ASAP once IMAP returns
        self._stats.invalidate()
        sse.publish("outbox", {})                   # the tray shows it
        return {"ok": True, "msg": "queued", "status": "queued", "undoable": bool(undo)}


    def _readmut_enqueue_flags(self, pk, flags, mutation_id):
        """IMAP was unreachable (or the process is read-only) — durably queue this flag change, show it
        on the local row at once, and report it queued. The row records the flags as they were, so a
        change the server refuses, or one the queue gives up on, puts them back."""
        c = self._store.conn()
        row = self._store.get_message(c, pk)
        if not row:
            c.close()
            return {"ok": False, "msg": "not found"}
        if not (isinstance(mutation_id, str) and 0 < len(mutation_id) <= 128):
            mutation_id = secrets.token_hex(16)
        created = self._store.queue_flags(c, row, flags, mutation_id, now_iso())
        c.commit()
        c.close()
        if created:
            self.flags_queued()
        return {"ok": True, "msg": "queued", "status": "queued", "undoable": False}


    def flags_queued(self):
        self._commands.kick()
        self._stats.invalidate()
        sse.publish("outbox", {})                   # the tray shows it


    def do_pending_retry(self, mid):
        """The tray's Retry: a change the queue gave up on goes back in. Returns (result, code)."""
        if not self._commands.retry(mid):
            return {"ok": False}, 404
        self.settled()
        return {"ok": True}, 200


    def do_pending_discard(self, mid):
        """The tray's Discard, on the account's gate. A message whose row went when it was queued, before
        migration 23 kept them, is read back. Returns (result, code)."""
        c = self._store.conn()
        row = self._store.command_get(c, mid)
        c.close()
        if row is None:
            return {"ok": False}, 404

        def job():
            gone = self._commands.discard(mid)
            if gone is None or gone["kind"] == "flag" or not gone["message_id"]:
                return gone is not None
            cc = self._store.conn()
            listed = cc.execute("SELECT 1 FROM messages WHERE account=? AND folder=? AND message_id=?",
                                (gone["account"], gone["src_folder"], gone["message_id"])).fetchone()
            cc.close()
            if not listed:
                try:
                    self._ingest.ingest_message(self._accounts.get(gone["account"]), gone["src_folder"], gone["message_id"])
                except Exception as e:                # the next open of the folder has another chance
                    print(f"[pending] reading a discarded move's message back: {self._accounts.quiet_error(e)}", flush=True)
            return True
        try:
            ok = gates.run(row["account"], job, priority=gates.Priority.INTERACTIVE, deadline=self.PENDING_WAIT)
        except gates.Busy:
            return {"ok": False, "busy": True}, 409
        if not ok:
            return {"ok": False}, 404
        self.settled()
        return {"ok": True}, 200


    def do_write(self, path, body):
        """Per-message mailbox writes (flags/seen/archive/move), serialized + optimistic. Each falls
        back to the durable offline queue if IMAP is unreachable, or the process is read-only."""
        try:
            pk = int(body.get("pk"))
        except (TypeError, ValueError):
            return {"ok": False, "msg": "bad pk"}
        _Q = {"/api/archive": ("archive", None), "/api/move": ("move", body.get("dest")),
              "/api/junk": ("junk", None)}
        if path == "/api/flag" and body.get("flag", "flagged") not in ("flagged", "seen"):
            # \Deleted, \Draft and \Answered are the app's to set (trash, drafts, sends), never a
            # request's: \Deleted from here would bypass Trash and undo.
            return {"ok": False, "msg": "unknown flag"}

        def write():
            """(ok, msg, undo, queued): queued is the answer when IMAP was down and the move waits in
            the offline queue instead."""
            if path in ("/api/seen", "/api/flag"):
                flags = ({"seen": bool(body.get("seen", True))} if path == "/api/seen"
                         else {body.get("flag", "flagged"): bool(body.get("on", True))})
                try:
                    return (*self._server.set_flags(pk, flags), None, None)
                except UNREACHABLE:           # IMAP down → queue it, as a move is (no IMAP here)
                    return None, None, None, self._readmut_enqueue_flags(pk, flags, body.get("mutation_id"))
            if path in _Q:
                op, dest = _Q[path]
                try:
                    if op == "archive":
                        return (*self._server.archive_message(pk), None)
                    if op == "move":
                        return (*self._server.move_message(pk, dest), None)
                    return (*self._server.junk_message(pk), None)
                except UNREACHABLE:           # IMAP down → queue it (still under the gate; no IMAP here)
                    return None, None, None, self._readmut_enqueue(op, pk, dest, body.get("undo_group"),
                                                              body.get("mutation_id"))
            return False, "unknown", None, None
        # no such message: the op finds that out under the gate, as it always has
        ok, msg, undo, queued = gates.run(self._server.account_of(pk) or gates.EVERY_ACCOUNT, write,
                                          priority=gates.Priority.INTERACTIVE)
        if queued is not None:
            return queued
        if ok:
            if undo:
                self._undo.push(undo, body.get("undo_group"))
            self._stats.invalidate()
            self._folders.invalidate()      # seen/archive/move/junk changed IMAP UNSEEN/MESSAGES → drop stale rail badges
        return {"ok": ok, "msg": msg, "undoable": bool(undo)}


    def do_trash(self, body):
        """Move one message to the account's Trash, serialized with every other live-IMAP op. Falls
        back to the durable offline queue if IMAP is unreachable, like the move-type ops in do_write."""
        pk = int(body.get("pk"))

        def trash():
            """(ok, msg, undo, queued): queued is the answer when IMAP was down, as in do_write."""
            try:
                return (*self._server.trash_message(pk), None)
            except UNREACHABLE:       # IMAP down → durable offline queue (still under the gate; no IMAP here)
                return None, None, None, self._readmut_enqueue("trash", pk, None, body.get("undo_group"),
                                                          body.get("mutation_id"))
        ok, msg, undo, queued = gates.run(self._server.account_of(pk) or gates.EVERY_ACCOUNT, trash,
                                          priority=gates.Priority.INTERACTIVE)
        if queued is not None:
            return queued
        if ok:
            self._undo.push(undo, body.get("undo_group"))
            self._stats.invalidate()
            self._folders.invalidate()   # trash removed a message from INBOX → refresh rail badges
        return {"ok": ok, "msg": msg, "undoable": bool(undo)}


    def do_thread_file(self, path, body):
        """Archive or trash a whole conversation: every message sharing the anchor's thread_id in the
        anchor's folder, which is what a conversation row stands for. Members are resolved here, never
        sent by the client (pk isn't stable, and a list row only knows its newest message). Each one
        goes through the per-message op, so locking, the offline queue and undo descriptors are
        unchanged; one undo group spans them all, so a single Undo restores the conversation. A
        message without a thread_id is a conversation of one, never the folder's unthreaded pool."""
        try:
            pk = int(body.get("pk"))
        except (TypeError, ValueError):
            return {"ok": False, "msg": "bad pk"}
        c = self._store.conn()
        row = self._store.get_message(c, pk)
        if not row:
            pks = []
        elif (row["thread_id"] or "").strip():
            pks = [r["pk"] for r in c.execute(
                "SELECT pk FROM messages WHERE account=? AND folder=? AND thread_id=? "
                "AND pending_removal IS NULL ORDER BY ts DESC, pk DESC", (row["account"], row["folder"], row["thread_id"]))]
        else:
            pks = [pk]
        c.close()
        if not pks:
            return {"ok": False, "msg": "not found"}
        group = body.get("undo_group") or "thread:" + secrets.token_hex(6)
        filed, queued, undoable, msg = [], 0, False, ""
        for p in pks:
            one = {"pk": p, "undo_group": group, "mutation_id": body.get("mutation_id")}
            res = self.do_write("/api/archive", one) if path == "/api/thread/archive" else self.do_trash(one)
            if res.get("ok"):
                filed.append(p)
                queued += res.get("status") == "queued"
                undoable = undoable or bool(res.get("undoable"))
            else:
                msg = res.get("msg") or msg
        return {"ok": bool(filed), "msg": msg if not filed else ("queued" if queued else "filed"),
                "pks": filed, "count": len(filed), "failed": len(pks) - len(filed),
                "queued": queued, "undoable": undoable}


    def settled(self, _event=None):
        """Applied or Restored, or the tray's Retry or Discard: a queued change reached the
        server or won't, and its message is listed again; or a person asked it again or dropped it."""
        self._stats.invalidate()
        sse.publish("commands-changed", {})  # a queued move landed: the counts and lists may differ

    def _carry_out_one(self, verb, one):
        """One message of a plan, through the route a click on that message would take."""
        if verb == "trash":
            return self.do_trash(one)
        if verb in ("read", "unread"):
            return self.do_write("/api/seen", {**one, "seen": verb == "read"})
        if verb == "star":
            return self.do_write("/api/flag", {**one, "flag": "flagged", "on": True})
        return self.do_write("/api/junk" if verb == "junk" else "/api/archive", one)

    def carry_out(self, verb, preset, messages, undo_group=None):
        """Carry out what a plan froze, and only that, whatever else the request names. A copy no longer
        in the folder the preview saw it in is left where it is: someone moved it, and a verb that was
        right for the Inbox may not be for wherever it went."""
        c = self._store.conn()
        try:
            pks = [p for p in (self._store.pk_in(c, key, folder) for key, folder in messages) if p is not None]
        finally:
            c.close()
        group = undo_group or "plan:" + secrets.token_hex(6)
        done = {"ok": True, "verb": verb, "count": 0, "failed": 0, "gone": len(messages) - len(pks),
                "queued": 0, "undoable": False}
        if verb == "snooze":
            if pks:
                res, _ = self._attention.snooze({"pks": pks, "preset": preset, "undo_group": group})
                done.update(count=res.get("count", 0), failed=len(pks) - res.get("count", 0), undoable=bool(res.get("ok")))
            return done
        for pk in pks:
            try:
                res = self._carry_out_one(verb, {"pk": pk, "undo_group": group})
            except Exception as e:                 # the rest still go: this run has no second try
                print(f"[do] {verb} failed: {type(e).__name__}", flush=True)
                res = {}
            if res.get("ok"):
                done["count"] += 1
                done["queued"] += res.get("status") == "queued"
                done["undoable"] = done["undoable"] or bool(res.get("undoable"))
            else:
                done["failed"] += 1
        return done
