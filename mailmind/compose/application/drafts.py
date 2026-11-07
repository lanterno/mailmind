"""A draft: saving it on the server, removing it, and opening it back into the composer from its own bytes."""
import enum
import time
from collections.abc import Mapping
from typing import Any, Literal

from mailmind.compose.application.composing import Composing
from mailmind.compose.application.outbox import Outbox
from mailmind.compose.application.ports import Account, Accounts, Book, Copies, Messages, Screen
from mailmind.compose.domain import draft, draft_journal, resume as reopening
from mailmind.compose.domain.builder import Built, Composed, build_message, thread_ids
from mailmind.content.headers import header_chips, header_text
from mailmind.kernel.errors import Conflict, Locked, NotFound, Problem, Refused, Unreachable, Unreadable
from mailmind.kernel.parsed import as_int, required_int
from mailmind.kernel.row import Row
from mailmind.kernel.when import now_iso
from mailmind.platform import profile
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger
from mailmind.platform.workers import gates

log = get_logger("draft")
resume_log = get_logger("resume")
_LIVE_TIMEOUT = 10
PUSH_WAIT = 2.5              # how long a save someone asked for waits for the account's gate, before the job has it
_NOT_LOADED = "Couldn’t load this draft from the mail server. Try again in a moment."


class _Gate(enum.Enum):
    WIDER = enum.auto()      # the row now needs a wider gate than the job held


type Pushed = int | None | Literal[False]    # the new copy's UID (None if the server didn't say), or False: it has to wait


def _now() -> int:
    return int(time.time())


def _scope_of(row: Row, waiting: Row) -> str:
    return gates.EVERY_ACCOUNT if draft.spans_accounts(row, waiting) else waiting["account"]


class Drafts:
    def __init__(self, uow: UnitOfWork, composing: Composing, outbox: Outbox, book: Book, accounts: Accounts, mail: Messages,
                 copies: Copies, screen: Screen):
        self._uow, self._composing, self._outbox, self._book = uow, composing, outbox, book
        self._accounts, self._mail, self._copies, self._screen = accounts, mail, copies, screen

    def is_draft(self, row: Row) -> bool:
        return draft.is_draft(row, self._screen.plays_drafts)

    def _ours_to_remove(self, d: Row) -> bool:
        """The outbox's draft_copy_is_ours, and a copy in a Drafts folder only the last LIST can name."""
        return self._outbox.draft_copy_is_ours(d) or self._screen.plays_drafts(d["account"], d["folder"])

    def info(self, row: Row) -> dict[str, Any]:
        """For a draft, expose its draft_id + mode so the client can open it back into the composer.
        Lazily registers a drafts row if one isn't tracked yet."""
        if not self.is_draft(row):
            return {}
        with self._uow.read() as c:                  # opening a tracked draft writes nothing, so it takes no lock
            d = self._book.draft_by_msgid(c, row["account"], row["message_id"])
        if d:
            return {"draft_id": d["id"], "draft_mode": d["mode"] or "new", "is_draft": True}
        now = now_iso()
        uidvalidity = self._mail.uidvalidity(row["account"], row["folder"])
        with self._uow.write() as c:
            did = self._book.draft_upsert(c, draft.tracked(row["account"], row["message_id"], row["subject"], "new", now,
                                                           folder=row["folder"], uid=row["uid"], uidvalidity=uidvalidity))
        return {"draft_id": did, "draft_mode": "new", "is_draft": True}

    def save(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Save a draft, or discard it. Either is local and durable the moment it answers: the journal holds it, and
        the server's copy follows (decision D5). An autosave leaves that to the push job, at most every minute; any other
        save tries now, for a moment."""
        if body.get("delete") and body.get("draft_id"):
            return self._discard(required_int(body["draft_id"]))
        try:
            spec, in_reply_to_pk, acct = self._composing.spec(body)
            if draft.is_empty(spec):
                return {"ok": True, "draft_id": body.get("draft_id"), "empty": True}
            built = build_message(spec, draft=True)
        except ValueError as e:                       # our own words: they hold nothing secret
            raise Refused(str(e)) from e
        did, version = self._accept(body, spec, built, acct, in_reply_to_pk)
        answer: dict[str, Any] = {"ok": True, "draft_id": did, "version": version, "message_id": built["message_id"], "pushed": False}
        if not body.get("autosave") and profile.writes_allowed():
            uid = self._try_push(did, priority=gates.Priority.INTERACTIVE, deadline=PUSH_WAIT)
            if uid is not False:
                answer.update(pushed=True, uid=uid)
        if body.get("resume") is None and spec["tail"]["text"]:
            answer.update(draft.handed_over(spec))
        return answer

    def _accept(self, body: Mapping[str, Any], spec: Composed, built: Built, acct: Account,
                in_reply_to_pk: int | None) -> tuple[int, int]:
        """Write the save to the journal, in one transaction with the draft's row. Returns the row's id and its version.
        A save that may not go in raises, with nothing written."""
        did = required_int(body["draft_id"]) if body.get("draft_id") else None
        key = draft.composer_key(body)
        base = body.get("base_version")
        now, stamp = _now(), now_iso()
        record = draft_journal.record_of(spec, built)
        with self._uow.write() as c:
            prev = self._book.draft_get(c, did) if did else (self._book.draft_by_key(c, key) if key else None)
            waiting = self._book.journal_get(c, prev["id"]) if prev else None
            if draft_journal.asks_removal(waiting):
                prev = waiting = None
            if prev and self._book.outbox_holds_draft(c, prev["id"]):
                raise Locked(draft.HELD)
            if prev and draft.is_stale(prev, base, key):
                raise Conflict(draft.CHANGED_ELSEWHERE, conflict=True, version=prev["version"])
            version = draft.next_version(prev)
            if prev:
                new_id = prev["id"]
            else:
                new_id = self._book.draft_upsert(c, draft.tracked(acct["email"], built["message_id"], spec["subject"],
                                                                  body.get("mode", "new"), stamp, in_reply_to_pk=in_reply_to_pk))
                if key:
                    self._book.draft_set_key(c, new_id, key)
            self._book.draft_note_save(c, new_id, body.get("mode", "new"), in_reply_to_pk, spec["subject"], version, key, stamp)
            due, attempts = draft_journal.after_save(bool(body.get("autosave")), now, waiting, prev["pushed_at"] if prev else None)
            self._book.journal_put(c, new_id, version, acct["email"], built["message_id"], built["raw"], record["raw_sha"],
                                   record["own_text"], record["tail_text"], record["tail_html"], record["bcc"], now, due, attempts)
            return new_id, version

    def _discard(self, did: int) -> dict[str, Any]:
        """Discard a draft. A copy on the server goes first and the row after it, so a server that's away
        leaves the journal asking until it's back; with no copy there's nothing to wait for."""
        with self._uow.write() as c:
            row = self._book.draft_get(c, did)
            if row is None:
                return {"ok": True, "deleted": True}
            waiting = self._book.journal_get(c, did)
            if draft_journal.copy_may_be_there(row, waiting):
                self._book.journal_discard(c, did, draft.next_version(row), row["account"], _now())
                self._book.draft_note_discard(c, did, draft.next_version(row), now_iso())
                there = True
            else:
                self._book.draft_delete(c, did)
                there = False
        if there and profile.writes_allowed():
            self._try_push(did, priority=gates.Priority.INTERACTIVE, deadline=PUSH_WAIT)
        self._screen.outbox_changed()
        return {"ok": True, "deleted": True}

    def _try_push(self, did: int, *, priority: int, deadline: float | None = None, wait: bool = True) -> Pushed:
        """Do what the journal asks for this draft, as a job on the gate. Returns the new copy's UID (None if the server
        didn't say), or False when it has to wait: for the gate, for a server, or because nothing is asked any more."""
        try:
            with self._uow.read() as c:
                waiting, row = self._book.journal_get(c, did), self._book.draft_get(c, did)
            if waiting is None or row is None:
                return False
            scope = _scope_of(row, waiting)
            done = gates.run(scope, lambda: self._on_gate(did, scope), priority=priority, wait=wait, deadline=deadline)
            if done is _Gate.WIDER:                  # another save moved the row to another account while this waited
                done = gates.run(gates.EVERY_ACCOUNT, lambda: self._on_gate(did, gates.EVERY_ACCOUNT),
                                 priority=priority, wait=wait, deadline=deadline)
            return False if done is _Gate.WIDER else done   # no gate is wider than every account's: it can't say so
        except gates.Busy:
            return False
        except Exception as e:
            log.warning("push failed: %(why)s", {"why": self._screen.quiet_error(e)})
            with self._uow.write() as c:             # counts on from what it reads, so no one comes between
                now_waiting = self._book.journal_get(c, did)
                if now_waiting is not None:
                    attempts = now_waiting["attempts"] + 1
                    self._book.journal_failed(c, did, attempts, draft_journal.due_after_failing(_now(), attempts))
            self._screen.outbox_changed()
            return False

    def _on_gate(self, did: int, scope: str) -> Pushed | _Gate:
        with self._uow.read() as c:
            waiting, row = self._book.journal_get(c, did), self._book.draft_get(c, did)
        if waiting is None or row is None:           # a job this one waited for did it, or the draft has gone
            return False
        if scope != gates.EVERY_ACCOUNT and _scope_of(row, waiting) != scope:
            return _Gate.WIDER
        if draft_journal.asks_removal(waiting):
            self._remove(row, waiting)
            return False
        return self._push(row, waiting)

    def _push(self, prev: Row, j: Row) -> Pushed:
        """APPEND the save, drop the copy it replaces, and move the row with them. Returns the new copy's UID."""
        with self._uow.write() as c:
            going = self._book.journal_tried(c, prev["id"], j["version"], j["message_id"])
        if not going:                                # a newer save or a discard came first: the next try takes that
            return False
        acct = self._accounts.get(j["account"])
        folder, uid, uidv, replaced = self._copies.append_to_drafts(
            acct, j["raw"], **draft.replaced_in_place(prev, j), message_id=j["message_id"], tried=j["tried"])
        self._screen.touched(j["account"], folder)   # its list shows the new copy on its next open
        gone = [(j["account"], folder, replaced)] if replaced else []   # and no longer the copies it replaced
        # Only now that the new copy is saved, so a save that fails never leaves no copy at all. Best-effort; a message
        # that isn't a draft at all stays.
        if draft.left_elsewhere(prev, j, folder) and self._ours_to_remove(prev):
            try:
                if self._copies.delete_draft_uid(self._accounts.get(prev["account"]), prev["folder"], prev["uid"],
                                                 prev["uidvalidity"]):
                    gone.append((prev["account"], prev["folder"], prev["uid"]))
            except Exception as e:
                log.warning("old copy not removed: %(why)s", {"why": self._screen.quiet_error(e)})
        # the row moves with the copy, under the same gate: Edit re-checks it under the gate
        with self._uow.write() as c:
            self._book.draft_settle_push(c, prev["id"], j["account"], folder, uid, j["message_id"], uidv, _now())
            self._book.draft_set_resume(c, prev["id"], j["own_text"], j["tail_text"], j["tail_html"], j["bcc"], j["raw_sha"])
            self._book.journal_settled(c, prev["id"], j["version"])
            for account, in_folder, old_uid in gone:
                self._mail.remove_copies(c, account, in_folder, [old_uid])
        for account, in_folder, _ in gone:
            self._screen.list_changed(account, in_folder)
        self._screen.stats_changed()
        self._screen.outbox_changed()
        return uid

    def _reach(self, email: str) -> Account | None:
        try:
            return self._accounts.get(email)
        except KeyError:
            return None

    def _remove(self, row: Row, j: Row) -> None:
        """A discard's copy goes, and then the row. A connection that fails raises, and the journal asks again. A server
        that won't remove it has the last word: the row is forgotten, and the copy stays listed. An account that has gone
        has nothing left to ask."""
        removed = False
        acct = self._reach(row["account"])
        if acct is not None and row["uid"] and self._ours_to_remove(row):
            removed = self._copies.delete_draft_uid(acct, row["folder"], row["uid"], row["uidvalidity"])
        left_in = self._reach(j["account"])          # From may have changed since the row's copy
        if left_in is not None and j["tried"]:
            self._copies.delete_left_copy(left_in, j["tried"])
        with self._uow.write() as c:
            self._book.draft_delete(c, row["id"])
            if removed:
                self._mail.remove_copies(c, row["account"], row["folder"], [row["uid"]])
        if removed:
            self._screen.list_changed(row["account"], row["folder"])
        self._screen.outbox_changed()

    def push_round(self) -> int:
        """One round of the push job: whatever has come due. Returns the seconds until the next is."""
        now = _now()
        with self._uow.read() as c:
            due = self._book.journal_due(c, now, now + draft_journal.PUSH_EVERY)
        for r in due:
            self._try_push(r["draft_id"], priority=gates.Priority.AFTER_USER, wait=False)
        with self._uow.read() as c:
            at = self._book.journal_next_due(c, now, now + draft_journal.PUSH_EVERY)
        return draft_journal.next_round_in(now, at)

    def waiting(self) -> list[dict[str, Any]]:
        """What the tray names: what should have reached its server by now and hasn't."""
        with self._uow.read() as c:
            rows = self._book.journal_overdue(c, draft_journal.overdue_since(_now(), profile.writes_allowed()))
        return [draft_journal.listed(r) for r in rows]

    def _live(self, acct: Account, folder: str, uid: int | None) -> bool | None:
        """Is this exact draft copy still on the server? True, False, or None when that can't be told
        (unreachable, no UID). A Drafts list loaded before an autosave still shows the copy that save
        replaced, and reopening it would put an older text over the newer one. A stateless, read-only
        probe with a short timeout, like the account test, so it needs no IMAP gate: a black-holed
        host costs Edit seconds, not a minute of every other mail operation."""
        if uid is None:
            return None
        seen = self._copies.probe(acct, folder, uid, _LIVE_TIMEOUT)
        if seen is None:
            return None
        cur, there = seen
        return reopening.copy_is_there(cur, self._mail.uidvalidity(acct["email"], folder), there)

    def resume(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Open a saved draft back into the composer from its own stored bytes: your text, the quote
        or forwarded section it already carries (as text), every file as base64, To/Cc/Bcc as chips
        and its own threading headers. The original is looked up by (account, Message-ID) for the
        composer's sake only: sending a reopened draft never needs it. What MailMind recorded at its
        last save (_m20_draft_resume) is trusted only for the very copy it saved."""
        if body.get("draft_id") and body.get("pk") is None:
            return self._resume_saved(body["draft_id"])
        pk = as_int(body.get("pk"))
        if pk is None:
            raise Refused("Missing draft.")
        row = self._mail.message(pk)
        if not row:
            raise NotFound("That draft is no longer in Drafts.")
        if not self.is_draft(row):
            raise Refused("That message isn’t a draft.")
        try:
            acct = self._accounts.get(row["account"])
        except KeyError:
            raise Refused("That draft’s account isn’t set up in MailMind any more.")
        with self._uow.read() as c:
            before = self._book.draft_by_msgid(c, row["account"], row["message_id"])
            queued = before is not None and self._book.outbox_holds_draft(c, before["id"])
            asked = self._book.journal_get(c, before["id"]) if before is not None else None
        if queued:                                           # reopened and sent again, it would go out twice
            raise Locked(draft.HELD)
        if draft_journal.asks_removal(asked):
            raise Conflict(draft.DISCARDED)
        live = self._live(acct, row["folder"], row["uid"])  # unreachable (None) → the local copy

        def reread_then_fetch() -> None:
            """The body fetched, or Conflict when the copy changed."""
            # the probe ran before the gate: a save or a send that held it meanwhile may have replaced
            # or removed the copy the row tracked (do_draft and the outbox move the row under it too)
            with self._uow.read() as c:
                now = self._book.draft_get(c, before["id"]) if before is not None else None
            if live is False or reopening.copy_moved(before, now):
                self._mail.remove(pk)
                try:                                         # full: only a full scan brings back older UIDs
                    self._mail.sync_whole(acct, row["folder"])
                except Exception as e:
                    resume_log.warning("drafts resync failed: %(why)s", {"why": self._screen.quiet_error(e)})
                raise Conflict("This draft changed since the list loaded. Open it again from the refreshed list.")
            self._mail.fetch_body(pk)
        try:
            gates.run(acct["email"], reread_then_fetch, priority=gates.Priority.INTERACTIVE)
        except Problem:
            raise
        except self._mail.unreachable() as e:
            resume_log.warning("fetch failed: %(why)s", {"why": self._screen.quiet_error(e)})
            raise Unreachable(_NOT_LOADED)
        except Exception as e:                               # fetched, but its bytes don't parse: retrying won't help
            resume_log.warning("draft unreadable", exc_info=e)
            raise Unreadable(reopening.UNREADABLE)
        raw = self._mail.body(pk)
        if not raw:
            raise Unreachable(_NOT_LOADED)
        did = self.info(row)["draft_id"]                   # registers another app's draft
        with self._uow.read() as c:
            d = self._book.draft_get(c, did)
            j = self._book.journal_get(c, did)
        assert d is not None                               # info() has just made sure of it
        cur = self._mail.uidvalidity(row["account"], row["folder"])
        trust = reopening.trust(d, row, cur, raw)
        record = d
        if draft_journal.holds_a_save(j):
            if trust.exact:                                # the copy is as pushed: the journal may be newer
                raw, record = j["raw"], j
            else:                                          # another app changed it: neither one takes the other's place
                self._keep_apart(d, j)
        # the next save or send replaces the copy opened
        repoint = None if trust.same_copy else (row["folder"], row["uid"], cur, not trust.exact)
        return self._opened(raw, d, record, trust.exact, row["account"], row["subject"], repoint)

    def _resume_saved(self, draft_id: Any) -> dict[str, Any]:
        """Open a draft by its journal, for one that may not be on the server yet."""
        did = as_int(draft_id)
        if did is None:
            raise Refused("Missing draft.")
        with self._uow.read() as c:
            d, j = self._book.draft_get(c, did), self._book.journal_get(c, did)
            queued = d is not None and self._book.outbox_holds_draft(c, did)
        if queued:
            raise Locked(draft.HELD)
        if d is None or j is None:
            raise Conflict("That draft has reached the server. Open it from Drafts.")
        if draft_journal.asks_removal(j):
            raise Conflict(draft.DISCARDED)
        return self._opened(j["raw"], d, j, True, j["account"], d["subject"], None)

    def _keep_apart(self, d: Row, j: Row) -> None:
        """Another app changed the copy while a save waited here. Text can't be merged and neither may be lost, so the
        save goes on as a draft of its own, with no copy of the other's to replace."""
        with self._uow.write() as c:
            now = now_iso()
            new_id = self._book.draft_upsert(c, draft.tracked(j["account"], j["message_id"], d["subject"], d["mode"], now))
            self._book.draft_note_save(c, new_id, d["mode"], None, d["subject"], d["version"], None, now)
            self._book.journal_move(c, d["id"], new_id)

    def _opened(self, raw: bytes, d: Row, record: Row, exact: bool, account: str, subject_then: str | None,
                repoint: tuple[str, int, int | None, bool] | None) -> dict[str, Any]:
        """A draft's bytes, in the composer's terms. `record` is what MailMind knows of those very bytes."""
        try:
            msg, parsed = reopening.read(raw)
            files, inline = reopening.resume_files(msg, parsed)
        except Problem:
            raise
        except Exception as e:
            resume_log.warning("draft unreadable", exc_info=e)
            raise Unreadable(reopening.UNREADABLE)
        own, tail, how = reopening.split_to_fit(parsed["text"], record, exact, files)
        irt, refs = thread_ids(header_text(msg, "In-Reply-To"), header_text(msg, "References"))
        mode = reopening.mode_of(d["mode"], irt)
        original = self._mail.by_msgid(account, irt) if mode in reopening.REPLIES and irt else None
        bcc, dropped = reopening.bcc_of(msg, record["bcc"], exact)
        if repoint:
            folder, uid, uidvalidity, forget = repoint
            with self._uow.write() as c:
                self._book.draft_repoint(c, d, folder, uid, uidvalidity, forget=forget)
        return {"ok": True, "draft_id": d["id"], "version": d["version"], "mode": mode, "account": account,
                "subject": reopening.subject_of(msg, subject_then),
                "to": header_chips(msg, "To"), "cc": header_chips(msg, "Cc"), "bcc": bcc,
                "text": own, "tail_text": tail, "in_reply_to": irt or "", "references": " ".join(refs),
                "orig_pk": original["pk"] if original else None,
                "participants": reopening.participants_of(original) if original else [], "files": files, "split": how,
                "notes": {"inline": inline, "bcc_dropped": dropped, "plain": how == "whole" and bool(parsed["html"])}}
