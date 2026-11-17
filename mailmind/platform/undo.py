"""Undo: a stack in the database, so it outlives a restart, of what reverses each thing a person did (decision D3). It
knows nothing of what an entry means: each context registers the reversers of its own kinds, and a reverser runs on the
account's gate and answers (ok, account, folder). An entry leaves the stack, committed, before any server is asked.

Every function here but Undo's methods takes its caller's connection and leaves the commit to it. Standard library only.
"""
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from datetime import datetime, timedelta, timezone
from typing import Any

from mailmind.kernel.errors import Conflict, NotFound
from mailmind.kernel.row import Connection
from mailmind.platform import profile, sse
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger
from mailmind.platform.workers import gates

log = get_logger("undo")

KEEP_FOR = timedelta(hours=24)                # an entry older than this can't be undone (decision D3)

type Entry = dict[str, Any]                   # what reverses one thing a person did: its kind says whose it is
type Reversed = tuple[bool, str | None, str | None]     # (ok, the account it touched, the folder)
type Reverser = Callable[[Entry], Reversed]
type Together = Callable[[list[Entry]], Sequence[Reversed]]   # a run of one kind and one account at once, in its order


def _now():
    return datetime.now(timezone.utc).isoformat()


def _oldest_kept():
    return (datetime.now(timezone.utc) - KEEP_FOR).isoformat()


def _expire(c: Connection) -> None:
    c.execute("DELETE FROM undo_entries WHERE created_at < ?", (_oldest_kept(),))


def push(c: Connection, entry: Entry, group: str | None, keep: int) -> None:
    """Put `entry` on top of the undo stack, or, when the top gathers `group`, add it to that
    group's children. Then keep only the newest `keep` entries, none older than KEEP_FOR."""
    _expire(c)
    top = c.execute("SELECT id, grp, entry FROM undo_entries ORDER BY id DESC LIMIT 1").fetchone()
    if group and top is not None and top["grp"] == group:
        held = json.loads(top["entry"])
        held["children"].append(entry)
        c.execute("UPDATE undo_entries SET entry=? WHERE id=?", (json.dumps(held), top["id"]))
    elif group:
        c.execute("INSERT INTO undo_entries(grp, entry, created_at) VALUES(?,?,?)",
                  (group, json.dumps({"kind": "bulk", "group": group, "label": entry.get("label", "Updated"),
                                      "children": [entry]}), _now()))
    else:
        c.execute("INSERT INTO undo_entries(grp, entry, created_at) VALUES(NULL,?,?)", (json.dumps(entry), _now()))
    c.execute("DELETE FROM undo_entries WHERE id NOT IN (SELECT id FROM undo_entries ORDER BY id DESC LIMIT ?)",
              (keep,))


def push_many(c: Connection, entries: list[Entry], group: str, keep: int) -> None:
    """Put `entries` on the stack as `group`'s children at one write: what `push` of each in turn leaves, the group's
    entry written once rather than once a child."""
    if not entries:
        return
    push(c, entries[0], group, keep)
    if len(entries) == 1 or not group:
        for entry in entries[1:]:
            push(c, entry, group, keep)
        return
    held = c.execute("SELECT id, entry FROM undo_entries WHERE grp=? ORDER BY id DESC LIMIT 1", (group,)).fetchone()
    entry = json.loads(held["entry"])
    entry["children"].extend(entries[1:])
    c.execute("UPDATE undo_entries SET entry=? WHERE id=?", (json.dumps(entry), held["id"]))


def put_back(c: Connection, entry: Entry) -> None:
    """Put an entry taken off the stack back on top: it, or what's left of its group, can be tried
    again."""
    c.execute("INSERT INTO undo_entries(grp, entry, created_at) VALUES(?,?,?)",
              (entry.get("group") if entry.get("kind") == "bulk" else None, json.dumps(entry), _now()))


def pop(c: Connection) -> Entry | None:
    """Take the newest entry off the undo stack: the entry, or None when none is left that may be undone."""
    _expire(c)
    top = c.execute("SELECT id, entry FROM undo_entries ORDER BY id DESC LIMIT 1").fetchone()
    if top is None:
        return None
    c.execute("DELETE FROM undo_entries WHERE id=?", (top["id"],))
    return json.loads(top["entry"])


def pop_group(c: Connection, group: str) -> Entry | None:
    """Take one group off the stack, wherever it stands: its entry, or None when it has gone."""
    _expire(c)
    held = c.execute("SELECT id, entry FROM undo_entries WHERE grp=? ORDER BY id DESC LIMIT 1", (group,)).fetchone()
    if held is None:
        return None
    c.execute("DELETE FROM undo_entries WHERE id=?", (held["id"],))
    return json.loads(held["entry"])


def forget(c: Connection, names: Callable[[Entry], bool]) -> None:
    """There is nothing to reverse any more: take the newest entry `names(entry)` is true of off the stack, or out of
    its group."""
    for r in c.execute("SELECT id, entry FROM undo_entries ORDER BY id DESC").fetchall():
        held = json.loads(r["entry"])
        if held.get("kind") != "bulk":
            if names(held):
                c.execute("DELETE FROM undo_entries WHERE id=?", (r["id"],))
                return
            continue
        children = held["children"]
        hit = next((i for i in range(len(children) - 1, -1, -1) if names(children[i])), None)
        if hit is None:
            continue
        del children[hit]
        if children:
            c.execute("UPDATE undo_entries SET entry=? WHERE id=?", (json.dumps(held), r["id"]))
        else:
            c.execute("DELETE FROM undo_entries WHERE id=?", (r["id"],))
        return


def forget_account(c: Connection, email: str) -> None:
    """Every entry for `email`'s mail, as its account is removed: one of its own goes, and a group of its alone; another
    group keeps its other children."""
    def ours(e: object) -> bool:
        return isinstance(e, dict) and str(e.get("account") or "").lower() == email.lower()

    for r in c.execute("SELECT id, entry FROM undo_entries").fetchall():
        try:
            held = json.loads(r["entry"])
        except ValueError:
            continue
        children = held.get("children") if isinstance(held, dict) else None
        if ours(held):
            c.execute("DELETE FROM undo_entries WHERE id=?", (r["id"],))
        elif isinstance(children, list) and any(ours(e) for e in children):
            if kept := [e for e in children if not ours(e)]:
                c.execute("UPDATE undo_entries SET entry=? WHERE id=?", (json.dumps({**held, "children": kept}), r["id"]))
            else:
                c.execute("DELETE FROM undo_entries WHERE id=?", (r["id"],))


def entries(c: Connection) -> list[Entry]:
    """The undo stack, oldest first."""
    return [json.loads(r["entry"]) for r in c.execute("SELECT entry FROM undo_entries WHERE created_at >= ? ORDER BY id",
                                                      (_oldest_kept(),))]


class Undo:
    UNDO_MAX = 50                             # a group counts as one (decision D3)

    def __init__(self, uow: UnitOfWork, reversers: Mapping[str, Reverser], after: Callable[[], object],
                 quiet_error: Callable[[Exception], str], together: Mapping[str, Together] | None = None):
        """`reversers` maps an entry's kind to what reverses it, `after()` runs once something was, and `quiet_error(e)`
        is what may be said of an exception. `together` maps a kind whose entries in a group go back together, a run of
        them on one account at one turn of its gate, to what reverses them: a sweep's hundreds ask the server once, not
        once each."""
        self._uow = uow
        self._reversers = reversers
        self._together = dict(together or {})
        self._after = after
        self._quiet_error = quiet_error
        self._lock = threading.Lock()

    def _undo_db[T](self, fn: Callable[[Connection], T]) -> T:
        """Run fn(c) on the undo stack under its lock, commit, and return what fn returned."""
        with self._lock, self._uow.write() as c:
            return fn(c)


    def push(self, desc: Entry | None, group: str | None = None) -> None:
        """Push a reverse descriptor. With a group id, coalesce consecutive pushes into one
        bulk entry so a single Undo reverses the whole batch. The stack keeps the newest self.UNDO_MAX
        entries, in the database, so Undo still works after a restart."""
        if not desc:
            return
        self._undo_db(lambda c: push(c, desc, group, self.UNDO_MAX))


    def push_many(self, descs: list[Entry], group: str) -> None:
        """Push several under one group at one write, as push of each in turn would: a sweep's reversals."""
        self._undo_db(lambda c: push_many(c, descs, group, self.UNDO_MAX))


    def push_within(self, c: Connection, desc: Entry, group: str | None = None) -> None:
        """Push in the caller's transaction, which already holds the database's write lock."""
        push(c, desc, group, self.UNDO_MAX)


    def entries(self) -> list[Entry]:
        """The undo stack, oldest first."""
        with self._uow.read() as c:
            return entries(c)


    def _reverse_one(self, d: Entry) -> tuple[bool, str | None, str | None]:
        """Run one descriptor's inverse as a job on its account's IMAP gate. Returns (ok,
        affected_account, src_folder)."""
        return gates.run(d.get("account") or gates.EVERY_ACCOUNT, lambda: self._reverse_held(d),
                         priority=gates.Priority.INTERACTIVE)


    def _reverse_held(self, d: Entry) -> tuple[bool, str | None, str | None]:
        reverse = self._reversers.get(d.get("kind", ""))
        if reverse:
            return reverse(d)
        return False, d.get("account"), None


    def _reverse_members(self, newest_first: list[Entry]) -> tuple[list[Reversed | None], bool]:
        """A group's members, newest first, each run of one kind and one account that goes back together at one turn of
        its gate and the rest one at a time: what each came to, None where it raised, and whether a read-only process
        refused any. One run's or member's trouble never stops the rest."""
        def gate(d: Entry) -> str:
            return d.get("account") or gates.EVERY_ACCOUNT

        out: list[Reversed | None] = []
        refused = False
        at = 0
        while at < len(newest_first):
            first = newest_first[at]
            kind = first.get("kind", "")
            together = self._together.get(kind)
            if together is None:
                try:
                    out.append(self._reverse_one(first))
                except profile.ReadOnlyError:
                    refused = True
                    out.append(None)
                except Exception as e:
                    log.warning("a group member: %(why)s", {"why": self._quiet_error(e)})
                    out.append(None)
                at += 1
                continue
            run = 1
            while (at + run < len(newest_first) and newest_first[at + run].get("kind", "") == kind
                   and gate(newest_first[at + run]) == gate(first)):
                run += 1
            members = newest_first[at:at + run]
            try:
                answers: list[Reversed | None] = list(gates.run(gate(first), partial(together, members),
                                                                priority=gates.Priority.INTERACTIVE))
            except profile.ReadOnlyError:
                refused = True
                answers = []
            except Exception as e:
                log.warning("a group member: %(why)s", {"why": self._quiet_error(e)})
                answers = []
            out.extend(answers[k] if k < len(answers) else None for k in range(run))
            at += run
        return out, refused


    def undo_last(self) -> dict[str, Any]:
        """Pop the last undoable action and reverse it, and say what went back: a group says how many of it did and how
        many didn't. Raises NotFound (`empty`) when there is nothing to undo, and Conflict when none of it went back. What
        didn't go back is on the stack again for another try, and so is all of it when a reversal raised, which goes on up:
        ReadOnlyError when a read-only process would have to ask a server, and none of it went back."""
        return self._reverse(self._undo_db(pop))  # off the stack before any server is asked

    def undo_group(self, group: str) -> dict[str, Any]:
        """As undo_last, for one group wherever it stands on the stack: what was done after it stays done, and stays undoable."""
        return self._reverse(self._undo_db(lambda c: pop_group(c, group)))

    def _reverse(self, entry: Entry | None) -> dict[str, Any]:
        if entry is None:
            raise NotFound(empty=True)
        kept = False                              # what couldn't be reversed is back on the stack
        try:
            if entry.get("kind") == "bulk":
                acct = folder = None
                left = []                             # newest first, as they were tried
                newest_first = list(reversed(entry["children"]))
                answers, refused = self._reverse_members(newest_first)
                for child, answer in zip(newest_first, answers):
                    if answer is not None and answer[0]:
                        _, acct, folder = answer
                    else:
                        left.append(child)
                done = len(entry["children"]) - len(left)
                if left:                              # a smaller group of what's left, for a retry
                    self._undo_db(lambda c: put_back(c, {**entry, "children": left[::-1]}))
                    kept = True
                okk = done > 0
                if refused and not okk:               # each would have asked a server: nothing went back, and it says why
                    raise profile.ReadOnlyError("an undo")
                res = {"ok": True, "label": entry.get("label"), "account": acct, "folder": folder,
                       "reversed": done, "failed": len(left)}
            else:
                ok, acct, folder = self._reverse_one(entry)
                okk = ok
                res = {"ok": True, "label": entry.get("label"), "account": acct, "folder": folder}
            if not okk:
                if not kept:                      # couldn't reverse -> keep it so the user can retry
                    self._undo_db(lambda c: put_back(c, entry))
                raise Conflict("couldn’t undo — the message may have moved")
            self._after()
            sse.publish("undo-done", {})
            res["remaining"] = len(self.entries())
            return res
        except Conflict:
            raise
        except profile.ReadOnlyError:                 # refused before any server was asked: it stays to undo on a full run
            if not kept:
                self._undo_db(lambda c: put_back(c, entry))
            raise
        except Exception as e:
            if not kept:
                self._undo_db(lambda c: put_back(c, entry))
            log.error("error: %(why)s", {"why": self._quiet_error(e)})
            raise
