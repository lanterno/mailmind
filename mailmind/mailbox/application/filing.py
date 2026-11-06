"""Filing a message as a person asks, through the one command path (MailCommands), and what each outcome answers. The
tray's Retry and Discard of what is waiting are here too."""
import secrets

from mailmind.mailbox.application.commands import MailCommands
from mailmind.mailbox.application.folders import Folders
from mailmind.mailbox.application.ports import Accounts, Attention, StoredMail
from mailmind.mailbox.application.stats import LiveStats
from mailmind.mailbox.application.sync import Ingest
from mailmind.mailbox.application.undo import Undo
from mailmind.mailbox.domain import command
from mailmind.platform import sse
from mailmind.platform.workers import gates

_FILED = {"trash": "trashed", "archive": "archived", "junk": "junked", "move": "moved"}
_REFUSED = {"archive": "the server refused the archive"}
_NO_FOLDER = {"archive": "no archive folder on this account", "junk": "no junk folder on this account"}
_NOT_FOUND = {"ok": False, "msg": "not found", "undoable": False}


class Filing:
    PENDING_WAIT = 15       # seconds the tray's Discard waits for a try at the change to end

    def __init__(self, store: StoredMail, accounts: Accounts, commands: MailCommands, ingest: Ingest,
                 undo: Undo, stats: LiveStats, folders: Folders, attention: Attention):
        self._store = store
        self._accounts = accounts
        self._commands = commands
        self._ingest = ingest
        self._undo = undo
        self._stats = stats
        self._folders = folders
        self._attention = attention

    def _waiting(self, outcome, undoable):
        """The worker has it: the tray shows it, and it says how it went."""
        self.flags_queued()
        return {"ok": True, "msg": "queued", "status": outcome, "undoable": undoable}


    def _file(self, kind, pk, dest, undo_group, mutation_id, undo=True):
        """A move, through the command path: what the person hears of it."""
        push = (lambda c, entry: self._undo.push_within(c, entry, undo_group)) if undo else None
        accepted = self._commands.accept_move(kind, pk, dest, mutation_id, push)
        if accepted is None:
            return dict(_NOT_FOUND)
        if not accepted.created:
            return {"ok": True, "msg": "queued", "status": "queued", "undoable": False}
        outcome = self._commands.submit(accepted)
        if outcome == command.APPLIED:
            self._stats.invalidate()
            self._folders.invalidate()      # the move changed IMAP UNSEEN/MESSAGES: drop stale rail badges
            return {"ok": True, "msg": _FILED[kind], "undoable": accepted.undoable}
        if outcome in (command.QUEUED, command.WAITING):
            return self._waiting(outcome, accepted.undoable)
        if outcome == command.CANCELLED:
            return {"ok": True, "msg": "cancelled", "undoable": False}
        self._stats.invalidate()            # the message is listed again
        msg = (_NO_FOLDER.get(kind) if outcome == command.NO_FOLDER else None) or _REFUSED.get(kind, "the server refused the move")
        return {"ok": False, "msg": msg, "undoable": False}


    def _set_flags(self, pk, flags, mutation_id):
        """A flag change, through the command path: what the person hears of it."""
        accepted = self._commands.accept_flags(pk, flags, mutation_id)
        if accepted is None:
            return dict(_NOT_FOUND)
        if not accepted.created:
            return {"ok": True, "msg": "queued", "status": "queued", "undoable": False}
        outcome = self._commands.submit(accepted)
        if outcome == command.APPLIED:
            self._stats.invalidate()
            self._folders.invalidate()      # seen changed IMAP UNSEEN: drop stale rail badges
            return {"ok": True, "msg": "ok", "undoable": False}
        if outcome in (command.QUEUED, command.WAITING):
            return self._waiting(outcome, False)
        if outcome == command.CANCELLED:
            return {"ok": True, "msg": "cancelled", "undoable": False}
        return {"ok": False, "msg": "the server refused the flag change", "undoable": False}


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
        """Per-message mailbox writes (flags/seen/archive/move)."""
        try:
            pk = int(body.get("pk"))
        except (TypeError, ValueError):
            return {"ok": False, "msg": "bad pk"}
        if path == "/api/flag" and body.get("flag", "flagged") not in ("flagged", "seen"):
            # \Deleted, \Draft and \Answered are the app's to set (trash, drafts, sends), never a
            # request's: \Deleted from here would bypass Trash and undo.
            return {"ok": False, "msg": "unknown flag"}
        if path in ("/api/seen", "/api/flag"):
            flags = ({"seen": bool(body.get("seen", True))} if path == "/api/seen"
                     else {body.get("flag", "flagged"): bool(body.get("on", True))})
            return self._set_flags(pk, flags, body.get("mutation_id"))
        moves = {"/api/archive": ("archive", None), "/api/move": ("move", body.get("dest")), "/api/junk": ("junk", None)}
        if path not in moves:
            return {"ok": False, "msg": "unknown", "undoable": False}
        kind, dest = moves[path]
        if kind == "move" and not dest:
            return dict(_NOT_FOUND)
        return self._file(kind, pk, dest, body.get("undo_group"), body.get("mutation_id"))


    def do_trash(self, body):
        """Move one message to the account's Trash."""
        return self._file("trash", int(body.get("pk")), None, body.get("undo_group"), body.get("mutation_id"))


    def trash_now(self, pk):
        """Trash with nothing for Undo to reverse, as blocking a sender does: whether it went or will go."""
        return self._file("trash", int(pk), None, None, None, undo=False)["ok"]


    def do_thread_file(self, path, body):
        """Archive or trash a whole conversation: every message sharing the anchor's thread_id in the
        anchor's folder, which is what a conversation row stands for. Members are resolved here, never
        sent by the client (pk isn't stable, and a list row only knows its newest message). Each one
        goes through the per-message op, so the gate, the command path and undo descriptors are
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
