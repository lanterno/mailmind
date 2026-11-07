"""What reverses a move: the Mailbox's kinds of undo entry. One that hasn't reached the server is cancelled; one that has
gets a counter-move, from where its command says it went."""
import json
from typing import Any

from mailmind.kernel.ids import INBOX, FolderName
from mailmind.mailbox.application.commands import MailCommands
from mailmind.mailbox.application.ports import Accounts, StoredMail
from mailmind.mailbox.application.server_ops import ServerOps
from mailmind.mailbox.application.sync import Ingest
from mailmind.platform.db.uow import UnitOfWork


type Reversed = tuple[bool, str, str]       # (ok, the account it touched, the folder)


class Reversals:
    def __init__(self, uow: UnitOfWork, store: StoredMail, accounts: Accounts, server: ServerOps, commands: MailCommands,
                 ingest: Ingest):
        self._uow = uow
        self._store = store
        self._accounts = accounts
        self._server = server
        self._commands = commands
        self._ingest = ingest

    def _settled_with_command(self, d: dict[str, Any]) -> tuple[Reversed | None, dict[str, Any]]:
        """Reconcile an entry with the command path. If its move hasn't reached the server (waiting, being tried or
        given up), CANCEL it and list its message again: its row waited, marked, for the move to settle, so no server is
        asked. A row queued before migration 23 was dropped instead, and is re-ingested. Returns (the answer, or None
        when a counter-move is still needed; the entry, with where the move went and what the server did on the way)."""
        if not d.get("message_id"):
            return None, d
        with self._uow.read() as c:
            q = (self._store.command_get(c, d["command"]) if d.get("command")
                 else self._store.command_latest_move(c, d["account"], d["message_id"]))
        if q is not None and not q["applied"]:
            src: FolderName = d.get("src_folder") or INBOX
            with self._uow.write() as c:
                cancelled = self._commands.QUEUE.cancel_unapplied(c, q["id"])
                if cancelled:
                    self._store.restore_pending(c, q["id"])
                listed = cancelled and self._store.is_listed_in(c, d["account"], src, d["message_id"])
            if listed:
                return (True, d["account"], src), d
            if cancelled:
                try:
                    n = self._ingest.ingest_message(self._accounts.get(d["account"]), src, d["message_id"])
                except Exception:
                    n = 0
                if n:
                    return (True, d["account"], src), d
                if not d.get("dest_folder"):     # it left by no doing of ours: nothing names where to look, and asking for a
                    return (True, d["account"], src), d    # folder of no name would keep this entry on top of the stack
                # re-ingest found nothing (UID gone) → fall through to the normal reverse
            return None, d
        if q is not None:
            if q["dest"] and not d.get("dest_folder"):
                d = {**d, "dest_folder": q["dest"]}
            if json.loads(q["outcome"] or "{}").get("unseen"):
                d = {**d, "unseen": True}
        return None, d


    def move(self, d: dict[str, Any]) -> Reversed:
        done, d = self._settled_with_command(d)
        if done:
            return done
        ok, _ = self._server.move_by_msgid(d["account"], d["dest_folder"], d["src_folder"], d["message_id"],
                                           unseen=bool(d.get("unseen")))
        if not ok:
            return False, d["account"], d["src_folder"]
        try:   # re-ingest the exact restored message (watermark sync would miss a re-used UID)
            self._ingest.ingest_message(self._accounts.get(d["account"]), d["src_folder"], d["message_id"])
        except Exception:
            pass
        return True, d["account"], d["src_folder"]


    def gmail_archive(self, d: dict[str, Any]) -> Reversed:
        done, d = self._settled_with_command(d)
        if done:
            return done
        allmail = self._server.find_special(d["account"], "all") or "[Gmail]/All Mail"
        ok, _ = self._server.relabel_by_msgid(d["account"], allmail, d["message_id"], "\\Inbox")
        if not ok:
            return False, d["account"], "INBOX"
        try:   # Gmail keeps the original INBOX UID on re-label -> targeted re-ingest, not watermark sync
            self._ingest.ingest_message(self._accounts.get(d["account"]), INBOX, d["message_id"])
        except Exception:
            pass
        return True, d["account"], "INBOX"
