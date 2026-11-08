"""Filing a message as a person asks, through the one command path (MailCommands), and what each outcome answers. A message
that has gone raises NotFound, and one the server wouldn't take raises Conflict in the words for it: either way it is where it
was. The tray's Retry and Discard of what is waiting are here too."""
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from mailmind.kernel.errors import Conflict, NotFound, Problem, Refused
from mailmind.kernel.ids import FolderName, MessageKey
from mailmind.kernel.parsed import as_int, is_list, is_object
from mailmind.kernel.row import Connection
from mailmind.mailbox.application.commands import MailCommands
from mailmind.mailbox.application.folders import Folders
from mailmind.mailbox.application.ports import Accounts, Attention, Result, StoredMail
from mailmind.mailbox.application.stats import LiveStats
from mailmind.mailbox.application.sync import Ingest
from mailmind.mailbox.domain import command
from mailmind.platform import sse
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger
from mailmind.platform.undo import Undo
from mailmind.platform.workers import gates

pending_log = get_logger("pending")
do_log = get_logger("do")
_FILED = {"trash": "trashed", "archive": "archived", "junk": "junked"}
_REFUSED = {"archive": "the server refused the archive"}
_NO_FOLDER = {"archive": "no archive folder on this account", "junk": "no junk folder on this account"}
_GONE = "not found"
_NOT_FILED = (NotFound, Conflict)    # what filing one message raises: it is where it was


class Filing:
    PENDING_WAIT = 15       # seconds the tray's Discard waits for a try at the change to end
    BULK_MAX = 500          # rows one request may file: the page sends more as more requests, under one undo group

    def __init__(self, uow: UnitOfWork, store: StoredMail, accounts: Accounts, commands: MailCommands, ingest: Ingest,
                 undo: Undo, stats: LiveStats, folders: Folders, attention: Attention):
        self._uow = uow
        self._store = store
        self._accounts = accounts
        self._commands = commands
        self._ingest = ingest
        self._undo = undo
        self._stats = stats
        self._folders = folders
        self._attention = attention

    def _waiting(self, outcome: str, undoable: bool) -> dict[str, Any]:
        """The worker has it: the tray shows it, and it says how it went."""
        self.flags_queued()
        return {"ok": True, "msg": "queued", "status": outcome, "undoable": undoable}


    def _pushing(self, undo_group: str | None) -> Callable[[Connection, dict[str, Any]], None]:
        """What keeps a move's reversal under `undo_group`, in the transaction that accepts the move."""
        def push(c: Connection, entry: dict[str, Any]) -> None:
            self._undo.push_within(c, entry, undo_group)
        return push


    def _file(self, kind: str, pk: int, undo_group: str | None, mutation_id: object, undo: bool = True) -> dict[str, Any]:
        """A move, through the command path: what the person hears of it."""
        accepted = self._commands.accept_move(kind, pk, mutation_id, self._pushing(undo_group) if undo else None)
        return self._submit_move(kind, accepted)


    def _submit_move(self, kind: str, accepted: command.Accepted | None) -> dict[str, Any]:
        """A move accepted and committed: a try at it, and what the person hears of it."""
        if accepted is None:
            raise NotFound(_GONE)
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
        raise Conflict((_NO_FOLDER.get(kind) if outcome == command.NO_FOLDER else None)
                       or _REFUSED.get(kind, "the server refused the move"))


    def _set_flags(self, pk: int, flags: Mapping[str, bool], mutation_id: object) -> dict[str, Any]:
        """A flag change, through the command path: what the person hears of it."""
        accepted = self._commands.accept_flags(pk, flags, mutation_id)
        if accepted is None:
            raise NotFound(_GONE)
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
        raise Conflict("the server refused the flag change")


    def flags_queued(self) -> None:
        self._commands.kick()
        self._stats.invalidate()
        sse.publish("outbox", {})                   # the tray shows it


    def do_pending_retry(self, mid: int) -> Result:
        """The tray's Retry: a change the queue gave up on goes back in."""
        if not self._commands.retry(mid):
            raise NotFound()
        self.settled()
        return {"ok": True}


    def do_pending_discard(self, mid: int) -> Result:
        """The tray's Discard, on the account's gate. A message whose row went when it was queued, before
        migration 23 kept them, is read back. Raises gates.Busy if the gate stays held."""
        with self._uow.read() as c:
            row = self._store.command_get(c, mid)
        if row is None:
            raise NotFound()

        def job() -> bool:
            gone = self._commands.discard(mid)
            if gone is None or gone["kind"] == "flag" or not gone["message_id"]:
                return gone is not None
            with self._uow.read() as cc:
                listed = self._store.has_copy_in(cc, gone["account"], gone["src_folder"], gone["message_id"])
            if not listed:
                try:
                    self._ingest.ingest_message(self._accounts.get(gone["account"]), gone["src_folder"], gone["message_id"])
                except Exception as e:                # the next open of the folder has another chance
                    pending_log.warning("reading a discarded move's message back: %(why)s", {"why": self._accounts.quiet_error(e)})
            return True
        if not gates.run(row["account"], job, priority=gates.Priority.INTERACTIVE, deadline=self.PENDING_WAIT):
            raise NotFound()
        self.settled()
        return {"ok": True}


    def do_write(self, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """Per-message mailbox writes (flags/seen/archive/junk)."""
        pk = as_int(body.get("pk"))
        if pk is None:
            raise Refused("bad pk")
        if path == "/api/flag" and body.get("flag", "flagged") not in ("flagged", "seen"):
            # \Deleted, \Draft and \Answered are the app's to set (trash, drafts, sends), never a
            # request's: \Deleted from here would bypass Trash and undo.
            raise Refused("unknown flag")
        if path in ("/api/seen", "/api/flag"):
            flags = ({"seen": bool(body.get("seen", True))} if path == "/api/seen"
                     else {body.get("flag", "flagged"): bool(body.get("on", True))})
            return self._set_flags(pk, flags, body.get("mutation_id"))
        moves = {"/api/archive": "archive", "/api/junk": "junk"}
        if path not in moves:
            raise Refused("unknown")
        return self._file(moves[path], pk, body.get("undo_group"), body.get("mutation_id"))


    def do_trash(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Move one message to the account's Trash."""
        pk = as_int(body.get("pk"))
        if pk is None:
            raise Refused()
        return self._file("trash", pk, body.get("undo_group"), body.get("mutation_id"))


    def trash_now(self, pk: int) -> bool:
        """Trash with nothing for Undo to reverse, as blocking a sender does: whether it went or will go."""
        try:
            self._file("trash", int(pk), None, None, undo=False)
        except _NOT_FILED:
            return False
        return True


    def do_thread_file(self, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """Archive or trash a whole conversation: every message sharing the anchor's thread_id in the
        anchor's folder, which is what a conversation row stands for. Members are resolved here, never
        sent by the client (pk isn't stable, and a list row only knows its newest message). A message
        without a thread_id is a conversation of one, never the folder's unthreaded pool.

        One transaction accepts them all under one undo group, so they leave the lists together, a single
        Undo restores the conversation, and a crash midway leaves all of it as it was. Then each one is tried
        as filing one message is, on the gate with no transaction open, and each answers for itself: one the
        server refuses goes back to its folder alone, and the rest stay filed. When none was filed, it raises what the first
        one did."""
        pk = as_int(body.get("pk"))
        if pk is None:
            raise Refused("bad pk")
        kind = "archive" if path == "/api/thread/archive" else "trash"
        push = self._pushing(body.get("undo_group") or "thread:" + secrets.token_hex(6))
        with self._uow.write() as c:                 # what it reads decides what it writes: no one comes between
            pks = self._conversation_of(c, pk)
            accepted = [(p, self._commands.accept_move_within(c, kind, p, body.get("mutation_id"), push)) for p in pks]
        return self._heard(accepted, self._try_each(kind, accepted))


    def file_many(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Archive, trash or junk the marked rows as one: `rows` is [{pk, whole}], `whole` for a row that stands for its
        conversation in its folder, which junk never does. As with a conversation, one transaction accepts them all under
        one undo group and then each is tried on its own. A row counts as failed if none of its messages was filed, and is
        `partial` if some were. When no row was, it raises what the first message did."""
        verb, rows = body.get("verb"), body.get("rows")
        if verb not in ("archive", "trash", "junk") or not is_list(rows) or not rows or len(rows) > self.BULK_MAX:
            raise Refused()
        wanted: list[tuple[int, bool]] = []
        for one in rows:
            if not is_object(one):
                raise Refused()
            pk = as_int(one.get("pk"))
            if pk is None:
                raise Refused()
            wanted.append((pk, one.get("whole") is True and verb != "junk"))
        push = self._pushing(body.get("undo_group") or "bulk:" + secrets.token_hex(6))
        accepted: list[tuple[int, command.Accepted | None]] = []
        taken: set[int] = set()
        with self._uow.write() as c:                 # what it reads decides what it writes: no one comes between
            members = [self._conversation_of(c, pk) if whole else [pk] for pk, whole in wanted]    # before any leaves a list
            for pks in members:
                for p in pks:
                    if p not in taken:               # two rows of one conversation
                        taken.add(p)
                        accepted.append((p, self._commands.accept_move_within(c, verb, p, body.get("mutation_id"), push)))
        results = self._try_each(verb, accepted)
        heard = self._heard(accepted, results)
        each = [sum(not isinstance(results[p], Problem) for p in pks) for pks in members]
        return {**heard, "rows_failed": each.count(0),
                "partial": any(0 < n < len(pks) for n, pks in zip(each, members))}


    def _conversation_of(self, c: Connection, pk: int) -> list[int]:
        """The messages a conversation's row stands for: its thread in its folder. A message without a thread id is a
        conversation of one, never the folder's unthreaded pool."""
        row = self._store.get_message(c, pk)
        if not row:
            return []
        if (row["thread_id"] or "").strip():
            return self._store.thread_pks_in(c, row["account"], row["folder"], row["thread_id"])
        return [pk]


    def _try_each(self, kind: str,
                  accepted: Sequence[tuple[int, command.Accepted | None]]) -> dict[int, dict[str, Any] | Problem]:
        """Moves accepted and committed: a try at each, as filing one message is, on the gate with no transaction open. Whoever
        clicked waits about as long as for one: past FILE_WAIT the rest are left to the worker, so a busy gate or a server
        gone quiet costs one wait, not one for each message. What one that wasn't filed raised stands for it."""
        out: dict[int, dict[str, Any] | Problem] = {}
        until = time.monotonic() + self._commands.FILE_WAIT
        left = False
        for i, (p, one) in enumerate(accepted):
            try:
                if i and one is not None and one.created and time.monotonic() >= until:
                    out[p] = {"ok": True, "msg": "queued", "status": self._commands.leave_to_worker(one),
                              "undoable": one.undoable}
                    left = True
                else:
                    out[p] = self._submit_move(kind, one)
            except _NOT_FILED as no:
                out[p] = no
            except Exception as e:                   # it's back in the queue; the rest are claimed, and wait on this loop
                do_log.warning("%(what)s failed", {"what": kind}, exc_info=e)
                self.flags_queued()
                out[p] = {"ok": True, "status": command.QUEUED, "undoable": bool(one and one.undoable)}
        if left:
            self.flags_queued()
        return out


    def _heard(self, accepted: Sequence[tuple[int, command.Accepted | None]],
               results: Mapping[int, Mapping[str, Any] | Problem]) -> dict[str, Any]:
        """What the person hears of many moves: one the server refused went back alone, and the rest stay filed. With none
        filed, what the first raised, or NotFound when there was none to file."""
        filed = {p: said for p, _ in accepted if not isinstance(said := results[p], Problem)}
        if not filed:
            raise next((no for p, _ in accepted if isinstance(no := results[p], Problem)), NotFound(_GONE))
        queued = sum(said.get("status") == "queued" for said in filed.values())
        return {"ok": True, "msg": "queued" if queued else "filed",
                "pks": list(filed), "count": len(filed), "failed": len(accepted) - len(filed),
                "queued": queued, "undoable": any(said.get("undoable") for said in filed.values())}


    def settled(self, _event: object = None) -> None:
        """Applied or Restored, or the tray's Retry or Discard: a queued change reached the
        server or won't, and its message is listed again; or a person asked it again or dropped it."""
        self._stats.invalidate()
        sse.publish("commands-changed", {})  # a queued move landed: the counts and lists may differ

    def _carry_out_one(self, verb: str, one: Mapping[str, Any]) -> dict[str, Any]:
        """One message of a plan, through the route a click on that message would take."""
        if verb == "trash":
            return self.do_trash(one)
        if verb in ("read", "unread"):
            return self.do_write("/api/seen", {**one, "seen": verb == "read"})
        if verb == "star":
            return self.do_write("/api/flag", {**one, "flag": "flagged", "on": True})
        return self.do_write("/api/junk" if verb == "junk" else "/api/archive", one)

    def carry_out(self, verb: str, preset: str | None, messages: Sequence[tuple[MessageKey, FolderName]],
                  undo_group: str | None = None) -> dict[str, Any]:
        """Carry out what a plan froze, and only that, whatever else the request names. A copy no longer
        in the folder the preview saw it in is left where it is: someone moved it, and a verb that was
        right for the Inbox may not be for wherever it went."""
        with self._uow.read() as c:
            pks = [p for p in (self._store.pk_in(c, key, folder) for key, folder in messages) if p is not None]
        group = undo_group or "plan:" + secrets.token_hex(6)
        done: dict[str, Any] = {"ok": True, "verb": verb, "count": 0, "failed": 0, "gone": len(messages) - len(pks),
                "queued": 0, "undoable": False}
        if verb == "snooze":
            if pks:
                try:
                    res = self._attention.snooze({"pks": pks, "preset": preset, "undo_group": group})
                except Refused:            # a preset that says no time: none of them went
                    res = {}
                done.update(count=res.get("count", 0), failed=len(pks) - res.get("count", 0), undoable=bool(res.get("ok")))
            return done
        for pk in pks:
            try:
                res: dict[str, Any] = self._carry_out_one(verb, {"pk": pk, "undo_group": group})
            except _NOT_FILED:                     # counted, and nothing went wrong here
                res = {}
            except Exception as e:                 # the rest still go: this run has no second try
                do_log.warning("%(what)s failed", {"what": verb}, exc_info=e)
                res = {}
            if res.get("ok"):
                done["count"] += 1
                done["queued"] += res.get("status") == "queued"
                done["undoable"] = done["undoable"] or bool(res.get("undoable"))
            else:
                done["failed"] += 1
        return done
