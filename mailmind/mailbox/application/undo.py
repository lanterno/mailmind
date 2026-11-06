"""Undo: a stack in the database, so it outlives a restart, of what reverses each thing a person did. What isn't the
Mailbox's own (a snooze, a mute, a block) its own context reverses, handed in as `reversers`."""
import threading

from mailmind.mailbox.application.commands import MailCommands
from mailmind.mailbox.application.folders import Folders
from mailmind.mailbox.application.offline_queue import OfflineQueue
from mailmind.mailbox.application.ports import Accounts, StoredMail
from mailmind.mailbox.application.stats import LiveStats
from mailmind.mailbox.application.sync import Ingest
from mailmind.platform import sse
from mailmind.platform.workers import gates


class Undo:
    UNDO_MAX = 25

    def __init__(self, store: StoredMail, accounts: Accounts, commands: MailCommands, offline: OfflineQueue, ingest: Ingest,
                 stats: LiveStats, folders: Folders, reversers):
        self._store = store
        self._accounts = accounts
        self._commands = commands
        self._offline = offline
        self._ingest = ingest
        self._stats = stats
        self._folders = folders
        self._reversers = reversers
        self._lock = threading.Lock()

    def _undo_db(self, fn):
        """Run fn(c) on the undo stack under its lock, commit, and return what fn returned."""
        with self._lock:
            c = self._store.conn()
            try:
                out = fn(c)
                c.commit()
                return out
            finally:
                c.close()


    def push(self, desc, group=None):
        """Push a reverse descriptor. With a group id, coalesce consecutive pushes into one
        bulk entry so a single Undo reverses the whole batch. The stack keeps the newest self.UNDO_MAX
        entries, in the database, so Undo still works after a restart."""
        if not desc:
            return
        self._undo_db(lambda c: self._store.undo_push(c, desc, group, self.UNDO_MAX))


    def entries(self):
        """The undo stack, oldest first."""
        return self._undo_db(self._store.undo_entries)


    def _reverse_one(self, d):
        """Run one descriptor's inverse as a job on its account's IMAP gate. Returns (ok,
        affected_account, src_folder)."""
        return gates.run(d.get("account") or gates.EVERY_ACCOUNT, lambda: self._reverse_held(d),
                         priority=gates.Priority.INTERACTIVE)


    def _reverse_held(self, d):
        kind = d.get("kind")
        if kind in ("move", "gmail_archive") and d.get("message_id"):
            # Reconcile undo with the offline queue. If the forward op hasn't shipped yet (queued,
            # claimed-but-not-applied, or given up), CANCEL it and list its message again: its row
            # waited, marked, for the move to settle, so no server is asked. A row queued before
            # migration 23 was dropped instead, and is re-ingested. If it already shipped, recover the
            # dest the worker resolved online so the counter-move searches the RIGHT folder (the
            # enqueued desc had an empty dest_folder for junk/archive).
            c = self._store.conn()
            q = self._store.read_mut_latest_by_msgid(c, d["account"], d["message_id"])
            if q is not None and not q["applied"]:
                src = d.get("src_folder") or "INBOX"
                cancelled = self._offline.QUEUE.cancel_unapplied(c, q["id"])
                if cancelled:
                    self._store.restore_pending(c, q["id"])
                listed = cancelled and c.execute(
                    "SELECT 1 FROM messages WHERE account=? AND folder=? AND message_id=? "
                    "AND pending_removal IS NULL", (d["account"], src, d["message_id"])).fetchone()
                c.commit(); c.close()
                if listed:
                    return True, d["account"], src
                if cancelled:
                    try:
                        n = self._ingest.ingest_message(self._accounts.get(d["account"]), src, d["message_id"])
                    except Exception:
                        n = 0
                    if n:
                        return True, d["account"], src
                    # re-ingest found nothing (UID gone) → fall through to the normal reverse
            else:
                if q is not None and q["dest"] and not d.get("dest_folder"):
                    d = {**d, "dest_folder": q["dest"]}    # shipped: use the resolved dest
                c.close()
        if kind == "move":
            ok, _ = self._commands.move_by_msgid(d["account"], d["dest_folder"], d["src_folder"], d["message_id"],
                                          unseen=bool(d.get("unseen")))
            if not ok:
                return False, d["account"], d["src_folder"]
            try:   # re-ingest the exact restored message (watermark sync would miss a re-used UID)
                self._ingest.ingest_message(self._accounts.get(d["account"]), d["src_folder"], d["message_id"])
            except Exception:
                pass
            return True, d["account"], d["src_folder"]
        if kind == "gmail_archive":
            allmail = self._commands.find_special(d["account"], ("all mail", "\\all")) or "[Gmail]/All Mail"
            ok, _ = self._commands.relabel_by_msgid(d["account"], allmail, d["message_id"], "\\Inbox")
            if not ok:
                return False, d["account"], "INBOX"
            try:   # Gmail keeps the original INBOX UID on re-label -> targeted re-ingest, not watermark sync
                self._ingest.ingest_message(self._accounts.get(d["account"]), "INBOX", d["message_id"])
            except Exception:
                pass
            return True, d["account"], "INBOX"
        reverse = self._reversers.get(kind)
        if reverse:
            return reverse(d)
        return False, d.get("account"), None


    def undo_last(self):
        """Pop the last undoable action and reverse it. Returns (result, code)."""
        entry = self._undo_db(self._store.undo_pop)          # off the stack before any server is asked
        if entry is None:
            return {"ok": False, "empty": True}, 200
        kept = False                              # what couldn't be reversed is back on the stack
        try:
            if entry.get("kind") == "bulk":
                acct = folder = None
                left = []                             # newest first, as they were tried
                for child in reversed(entry["children"]):
                    try:
                        ok, a, f = self._reverse_one(child)
                    except Exception as e:            # one member's trouble never stops the rest
                        print(f"[undo] a group member: {self._accounts.quiet_error(e)}", flush=True)
                        ok = False
                    if ok:
                        acct, folder = a, f
                    else:
                        left.append(child)
                done = len(entry["children"]) - len(left)
                if left:                              # a smaller group of what's left, for a retry
                    self._undo_db(lambda c: self._store.undo_put_back(c, {**entry, "children": left[::-1]}))
                    kept = True
                okk = done > 0
                res = {"ok": okk, "label": entry.get("label"), "account": acct, "folder": folder,
                       "reversed": done, "failed": len(left)}
            else:
                ok, acct, folder = self._reverse_one(entry)
                okk = ok
                res = {"ok": ok, "label": entry.get("label"), "account": acct, "folder": folder}
            if not okk:
                if not kept:                      # couldn't reverse -> keep it so the user can retry
                    self._undo_db(lambda c: self._store.undo_put_back(c, entry))
                return {"ok": False, "error": "couldn’t undo — the message may have moved"}, 200
            self._stats.invalidate()
            self._folders.invalidate()      # undo re-added/removed a message → refresh rail badges
            sse.publish("undo-done", {})
            res["remaining"] = len(self.entries())
            return res, 200
        except Exception as e:
            if not kept:
                self._undo_db(lambda c: self._store.undo_put_back(c, entry))
            print(f"[undo] error: {self._accounts.quiet_error(e)}", flush=True)
            return {"ok": False, "error": "couldn’t undo"}, 200
