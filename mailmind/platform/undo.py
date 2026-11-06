"""Undo: a stack in the database, so it outlives a restart, of what reverses each thing a person did (decision D3). It
knows nothing of what an entry means: each context registers the reversers of its own kinds, and a reverser runs on the
account's gate and answers (ok, account, folder). An entry leaves the stack, committed, before any server is asked.

Every function here but Undo's methods takes its caller's connection and leaves the commit to it. Standard library only.
"""
import json
import threading
from datetime import datetime, timezone

from mailmind.platform import sse
from mailmind.platform.workers import gates


def _now():
    return datetime.now(timezone.utc).isoformat()


def push(c, entry, group, keep):
    """Put `entry` on top of the undo stack, or, when the top gathers `group`, add it to that
    group's children. Then keep only the newest `keep` entries."""
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


def put_back(c, entry):
    """Put an entry taken off the stack back on top: it, or what's left of its group, can be tried
    again."""
    c.execute("INSERT INTO undo_entries(grp, entry, created_at) VALUES(?,?,?)",
              (entry.get("group") if entry.get("kind") == "bulk" else None, json.dumps(entry), _now()))


def pop(c):
    """Take the newest entry off the undo stack: the entry, or None when the stack is empty."""
    top = c.execute("SELECT id, entry FROM undo_entries ORDER BY id DESC LIMIT 1").fetchone()
    if top is None:
        return None
    c.execute("DELETE FROM undo_entries WHERE id=?", (top["id"],))
    return json.loads(top["entry"])


def forget(c, names):
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


def entries(c):
    """The undo stack, oldest first."""
    return [json.loads(r["entry"]) for r in c.execute("SELECT entry FROM undo_entries ORDER BY id")]


class Undo:
    UNDO_MAX = 25

    def __init__(self, connect, reversers, after, quiet_error):
        """`reversers` maps an entry's kind to what reverses it, `after()` runs once something was, and `quiet_error(e)`
        is what may be said of an exception."""
        self._connect = connect
        self._reversers = reversers
        self._after = after
        self._quiet_error = quiet_error
        self._lock = threading.Lock()

    def _undo_db(self, fn):
        """Run fn(c) on the undo stack under its lock, commit, and return what fn returned."""
        with self._lock:
            c = self._connect()
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
        self._undo_db(lambda c: push(c, desc, group, self.UNDO_MAX))


    def push_within(self, c, desc, group=None):
        """Push in the caller's transaction, which already holds the database's write lock."""
        push(c, desc, group, self.UNDO_MAX)


    def entries(self):
        """The undo stack, oldest first."""
        return self._undo_db(entries)


    def _reverse_one(self, d):
        """Run one descriptor's inverse as a job on its account's IMAP gate. Returns (ok,
        affected_account, src_folder)."""
        return gates.run(d.get("account") or gates.EVERY_ACCOUNT, lambda: self._reverse_held(d),
                         priority=gates.Priority.INTERACTIVE)


    def _reverse_held(self, d):
        reverse = self._reversers.get(d.get("kind"))
        if reverse:
            return reverse(d)
        return False, d.get("account"), None


    def undo_last(self):
        """Pop the last undoable action and reverse it. Returns (result, code)."""
        entry = self._undo_db(pop)                # off the stack before any server is asked
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
                        print(f"[undo] a group member: {self._quiet_error(e)}", flush=True)
                        ok = False
                    if ok:
                        acct, folder = a, f
                    else:
                        left.append(child)
                done = len(entry["children"]) - len(left)
                if left:                              # a smaller group of what's left, for a retry
                    self._undo_db(lambda c: put_back(c, {**entry, "children": left[::-1]}))
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
                    self._undo_db(lambda c: put_back(c, entry))
                return {"ok": False, "error": "couldn’t undo — the message may have moved"}, 200
            self._after()
            sse.publish("undo-done", {})
            res["remaining"] = len(self.entries())
            return res, 200
        except Exception as e:
            if not kept:
                self._undo_db(lambda c: put_back(c, entry))
            print(f"[undo] error: {self._quiet_error(e)}", flush=True)
            return {"ok": False, "error": "couldn’t undo"}, 200
