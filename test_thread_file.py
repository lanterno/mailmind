"""Whole-conversation archive/trash (server.do_thread_file): which messages a conversation covers,
the single undo group, partial failure, and the offline queue. Hermetic — the database is a temp
file and the IMAP ops are replaced with local fakes. All addresses are fictional.
Run: python3 test_thread_file.py"""
import os
import tempfile

import store
store.DB = os.path.join(tempfile.mkdtemp(), "mailmind.db")   # before anything opens a connection
import server

ACCT = "ada@example.test"
_uid = [0]


def _fresh():
    """A new empty database, a clear undo stack, and fake IMAP ops that act like the real ones
    locally (drop the row, hand back an undo descriptor) and record what they were asked to file."""
    store.DB = os.path.join(tempfile.mkdtemp(), "mailmind.db")
    store.init()
    server._undo_stack.clear()
    calls = []

    def fake(label):
        def op(pk):
            c = store.conn()
            row = store.get_message(c, pk)
            if not row:
                c.close()
                return False, "not found", None
            store.delete_message(c, pk)
            c.commit()
            c.close()
            calls.append(pk)
            return True, label.lower(), {"kind": "move", "account": row["account"],
                                         "src_folder": row["folder"], "dest_folder": "Elsewhere",
                                         "message_id": row["message_id"], "label": label}
        return op
    server.mailops.archive_message = fake("Archived")
    server.mailops.trash_message = fake("Trashed")
    return calls


def _msg(folder, thread_id, ts, account=ACCT):
    _uid[0] += 1
    c = store.conn()
    cur = c.execute("INSERT INTO messages (account, kind, folder, uid, message_id, thread_id, ts, "
                    "from_addr, subject) VALUES (?,?,?,?,?,?,?,?,?)",
                    (account, "imap", folder, _uid[0], f"<m{_uid[0]}@example.test>", thread_id, ts,
                     "mira@example.test", "Saturday market"))
    c.commit()
    pk = cur.lastrowid
    c.close()
    return pk


def _left():
    c = store.conn()
    pks = {r["pk"] for r in c.execute("SELECT pk FROM messages")}
    c.close()
    return pks


# ── membership ────────────────────────────────────────────────────────────────
def test_files_every_message_of_the_thread_in_the_anchors_folder():
    calls = _fresh()
    a, b, newest = _msg("INBOX", "t1", 100), _msg("INBOX", "t1", 200), _msg("INBOX", "t1", 300)
    sent = _msg("Sent", "t1", 250)                   # your reply lives in Sent: not this row's
    other = _msg("INBOX", "t2", 400)                 # a different conversation
    elsewhere = _msg("INBOX", "t1", 150, account="ada@work.example.test")   # same id, other account
    r = server.do_thread_file("/api/thread/archive", {"pk": newest})
    assert r["ok"] and r["count"] == 3 and r["failed"] == 0, r
    assert sorted(r["pks"]) == sorted([a, b, newest]), r
    assert sorted(calls) == sorted([a, b, newest])
    assert _left() == {sent, other, elsewhere}

def test_any_message_can_anchor_the_conversation():
    _fresh()
    a, b = _msg("INBOX", "t1", 100), _msg("INBOX", "t1", 200)
    r = server.do_thread_file("/api/thread/trash", {"pk": a})
    assert sorted(r["pks"]) == sorted([a, b]), r

def test_message_without_a_thread_is_a_conversation_of_one():
    calls = _fresh()
    lone, other_lone = _msg("INBOX", None, 100), _msg("INBOX", "", 200)
    r = server.do_thread_file("/api/thread/archive", {"pk": lone})
    assert r["pks"] == [lone] and calls == [lone], r
    assert _left() == {other_lone}

def test_trash_route_uses_trash():
    calls = _fresh()
    a, b = _msg("INBOX", "t1", 100), _msg("INBOX", "t1", 200)
    r = server.do_thread_file("/api/thread/trash", {"pk": b})
    assert r["ok"] and sorted(calls) == sorted([a, b])
    assert server._undo_stack[-1]["label"] == "Trashed"


# ── undo ──────────────────────────────────────────────────────────────────────
def test_one_undo_entry_for_the_whole_conversation():
    _fresh()
    for ts in (100, 200, 300):
        _msg("INBOX", "t1", ts)
    r = server.do_thread_file("/api/thread/archive", {"pk": _msg("INBOX", "t1", 400)})
    assert r["undoable"] and len(server._undo_stack) == 1, server._undo_stack
    entry = server._undo_stack[0]
    assert entry["kind"] == "bulk" and len(entry["children"]) == 4, entry

def test_a_bulk_selection_shares_the_callers_undo_group():
    _fresh()
    x1 = _msg("INBOX", "tx", 100)
    _msg("INBOX", "tx", 200)
    y1 = _msg("INBOX", "ty", 300)
    server.do_thread_file("/api/thread/archive", {"pk": x1, "undo_group": "ug1"})
    server.do_thread_file("/api/thread/archive", {"pk": y1, "undo_group": "ug1"})
    assert len(server._undo_stack) == 1 and len(server._undo_stack[0]["children"]) == 3


# ── failures ──────────────────────────────────────────────────────────────────
def test_partial_failure_reports_what_was_filed():
    _fresh()
    a, b, c = _msg("INBOX", "t1", 100), _msg("INBOX", "t1", 200), _msg("INBOX", "t1", 300)
    real = server.mailops.archive_message
    server.mailops.archive_message = lambda pk: (False, "no archive folder on this account", None) if pk == b else real(pk)
    r = server.do_thread_file("/api/thread/archive", {"pk": c})
    assert r["ok"] and r["count"] == 2 and r["failed"] == 1, r
    assert sorted(r["pks"]) == sorted([a, c]) and _left() == {b}

def test_total_failure_is_not_ok():
    _fresh()
    a = _msg("INBOX", "t1", 100)
    server.mailops.archive_message = lambda pk: (False, "no archive folder on this account", None)
    r = server.do_thread_file("/api/thread/archive", {"pk": a})
    assert not r["ok"] and r["msg"] == "no archive folder on this account" and r["pks"] == [], r
    assert _left() == {a} and not server._undo_stack

def test_unreachable_imap_queues_each_message():
    _fresh()
    _msg("INBOX", "t1", 100)
    b = _msg("INBOX", "t1", 200)
    def down(pk):
        raise OSError("network is unreachable")
    server.mailops.trash_message = down
    r = server.do_thread_file("/api/thread/trash", {"pk": b})
    assert r["ok"] and r["count"] == 2 and r["queued"] == 2, r
    c = store.conn()
    queued = c.execute("SELECT COUNT(*) FROM read_mutations WHERE op='trash' AND applied=0").fetchone()[0]
    c.close()
    assert queued == 2 and _left() == set()

def test_missing_or_bad_anchor():
    _fresh()
    assert server.do_thread_file("/api/thread/archive", {"pk": 999})["ok"] is False
    assert server.do_thread_file("/api/thread/archive", {"pk": "x"})["msg"] == "bad pk"
    assert server.do_thread_file("/api/thread/archive", {})["msg"] == "bad pk"


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", fn.__name__, repr(e))
        except Exception as e:
            failed += 1
            print("ER:U", fn.__name__, repr(e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    sys.exit(1 if failed else 0)
