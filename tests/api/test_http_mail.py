"""The mail actions, over HTTP against the fake server: seen, flag, archive, junk, trash, whole
conversations, and Undo. Each step pins the answer, the IMAP commands that changed mail on the
server, and where the message is afterwards, on the server and in the database. The golden
files are in tests/golden/http_mail/.

Each provider files mail its own way. Ada's plain IMAP host moves the message. Bea's Proton Bridge
moves it, and marks anything copied into Trash as read. Cy's Gmail drops a label instead.
"""
import problems
from fixtures import world as w


def act(app, golden, world, label, path, body, *names):
    """POST `body` to `path`, then record the answer, the server writes it caused and where each
    of the corpus messages `names` is now."""
    since = world.mark()
    r = golden.record(label, app.post(path, body))
    golden.note(f"{label}: server writes", world.writes(since))
    for name in names:
        golden.note(f"{label}: where {name} is", world.where(name))
    return r


def undo(app, golden, world, label, *names):
    return act(app, golden, world, f"undo {label}", "/api/undo", {}, *names)


# ── flags ────────────────────────────────────────────────────────────────

def test_seen_and_flagged(app, golden, world):
    unread = "ada-inbox-02-lunch-confirm.eml"
    copy = world.copy(unread)
    act(app, golden, world, "mark read", "/api/seen", {**copy, "seen": True}, unread)
    act(app, golden, world, "mark unread", "/api/seen", {**copy, "seen": False}, unread)
    act(app, golden, world, "star", "/api/flag", {**copy, "flag": "flagged", "on": True}, unread)
    act(app, golden, world, "unstar", "/api/flag", {**copy, "flag": "flagged", "on": False}, unread)
    act(app, golden, world, "flag seen", "/api/flag", {**copy, "flag": "seen"}, unread)
    act(app, golden, world, "flag deleted", "/api/flag", {**copy, "flag": "deleted"}, unread)
    act(app, golden, world, "flag answered", "/api/flag", {**copy, "flag": "answered"}, unread)
    act(app, golden, world, "flag a bad key", "/api/flag", {"key": "x", "folder": "INBOX"})
    act(app, golden, world, "flag a missing message", "/api/seen", {"key": 999999, "folder": "INBOX"})
    act(app, golden, world, "undo with nothing to undo", "/api/undo", {})


def test_a_refused_flag_change_leaves_the_local_row_alone(app, world):
    name = "ada-inbox-02-lunch-confirm.eml"
    world.server.fail("UID STORE")
    r = app.post("/api/seen", {**world.copy(name), "seen": True})
    assert (r.status, r.json, world.row(name)["seen"]) == (409, problems.conflict("the server refused the flag change"), 0)


# ── archive, junk, trash, and their undo ─────────────────────────────

def test_archive_on_each_provider_and_undo(app, golden, world):
    for name in ("ada-inbox-05-receipt.eml", "bea-inbox-01-client.eml", "cy-inbox-03-unrelated.eml"):
        act(app, golden, world, f"archive {name}", "/api/archive", {**world.copy(name)}, name)
    for name in ("cy-inbox-03-unrelated.eml", "bea-inbox-01-client.eml", "ada-inbox-05-receipt.eml"):
        undo(app, golden, world, name, name)
    undo(app, golden, world, "once more than there is")


def test_junk_on_each_provider_and_undo(app, golden, world):
    for name in ("ada-inbox-12-scam.eml", "bea-inbox-02-security.eml", "cy-inbox-02-thread-end.eml"):
        act(app, golden, world, f"junk {name}", "/api/junk", {**world.copy(name)}, name)
    for name in ("cy-inbox-02-thread-end.eml", "bea-inbox-02-security.eml", "ada-inbox-12-scam.eml"):
        undo(app, golden, world, name, name)


def test_trash_on_each_provider_and_undo(app, golden, world):
    # Bea's message is unread: Proton marks what lands in Trash as read, and Undo must not
    # bring it back read.
    for name in ("ada-inbox-09-rfc2047-folded.eml", "bea-inbox-01-client.eml", "cy-inbox-03-unrelated.eml"):
        act(app, golden, world, f"trash {name}", "/api/trash", {**world.copy(name)}, name)
    for name in ("cy-inbox-03-unrelated.eml", "bea-inbox-01-client.eml", "ada-inbox-09-rfc2047-folded.eml"):
        undo(app, golden, world, name, name)
    act(app, golden, world, "trash without a key", "/api/trash", {})
    act(app, golden, world, "trash a missing message", "/api/trash", {"key": 999999, "folder": "INBOX"})


def test_undoing_a_proton_trash_brings_unread_mail_back_unread(app, world):
    name = "bea-inbox-01-client.eml"                       # unread in Bea's INBOX
    assert app.post("/api/trash", {**world.copy(name)}).json["ok"]
    assert app.post("/api/undo", {}).json["ok"]
    where = world.where(name)
    assert [(box, flags) for _, box, _, flags in where["server"]] == [("INBOX", [])]
    assert [r["seen"] for r in where["db"]] == [0]


def test_undoing_a_proton_trash_of_read_mail_leaves_it_read(app, world):
    name = "bea-inbox-02-security.eml"                     # read in Bea's INBOX
    assert app.post("/api/trash", {**world.copy(name)}).json["ok"]
    assert app.post("/api/undo", {}).json["ok"]
    where = world.where(name)
    assert [(box, flags) for _, box, _, flags in where["server"]] == [("INBOX", ["\\Seen"])]
    assert [r["seen"] for r in where["db"]] == [1]


def test_trash_and_undo_elsewhere_neither_read_nor_change_seen(app, world):
    name = "ada-inbox-01-lunch.eml"                        # on a server that leaves flags alone
    since = world.mark()
    assert app.post("/api/trash", {**world.copy(name)}).json["ok"]
    assert app.post("/api/undo", {}).json["ok"]
    sent = [c["line"].split(" ", 1)[1] for c in world.server.commands[since:]]
    assert not [line for line in sent if "STORE" in line.upper() or line.upper().endswith(" (FLAGS)")]


def test_actions_in_one_undo_group_undo_together(app, golden, world):
    names = ("ada-inbox-05-receipt.eml", "ada-inbox-09-rfc2047-folded.eml")
    for name in names:
        act(app, golden, world, f"archive {name} in group g1", "/api/archive",
            {**world.copy(name), "undo_group": "g1"}, name)
    undo(app, golden, world, "group g1", *names)


# ── when the server refuses or can't be reached ─────────────────────────────

def test_a_refused_move_keeps_the_message(app, golden, world):
    name = "ada-inbox-05-receipt.eml"
    world.server.fail("UID MOVE")
    world.server.fail("UID COPY")
    act(app, golden, world, "archive refused by the server", "/api/archive", {**world.copy(name)}, name)


def test_a_server_without_move_gets_copy_store_expunge(app, golden, world):
    name = "ada-inbox-05-receipt.eml"
    world.server.fail("UID MOVE", "BAD [CANNOT] MOVE not supported")
    act(app, golden, world, "archive by COPY", "/api/archive", {**world.copy(name)}, name)
    undo(app, golden, world, "the copied archive", name)


def test_an_unreachable_server_queues_the_move(app, golden, world):
    from mailmind.bootstrap.mailbox import store
    name = "ada-inbox-05-receipt.eml"
    world.server.drop("SELECT", user=w.ADA)
    act(app, golden, world, "archive while the server drops us", "/api/archive",
        {**world.copy(name), "mutation_id": "m-1"}, name)
    c = store.conn()
    try:
        golden.note("queued mutations", [
            {k: r[k] for k in ("account", "idempotency_key", "kind", "message_id", "src_folder", "dest", "applied")}
            for r in c.execute("SELECT * FROM mail_commands ORDER BY id")])
    finally:
        c.close()


# ── whole conversations ────────────────────────────────────────────────

def test_archive_and_trash_a_whole_conversation(app, golden, world):
    lunch = ("ada-inbox-01-lunch.eml", "ada-inbox-02-lunch-confirm.eml", "ada-sent-01-lunch-reply.eml")
    act(app, golden, world, "archive the lunch conversation", "/api/thread/archive",
        {**world.copy("ada-inbox-02-lunch-confirm.eml"), "undo_group": "t1"}, *lunch)
    undo(app, golden, world, "the lunch conversation", *lunch)
    venue = ("cy-inbox-01-thread-start.eml", "cy-inbox-02-thread-end.eml", "cy-sent-01-thread-reply.eml")
    act(app, golden, world, "trash the venue conversation", "/api/thread/trash",
        {**world.copy("cy-inbox-02-thread-end.eml"), "undo_group": "t2"}, *venue)
    undo(app, golden, world, "the venue conversation", *venue)
    act(app, golden, world, "archive a conversation of a missing message", "/api/thread/archive",
        {"key": 999999, "folder": "INBOX"})
    act(app, golden, world, "archive a conversation with a bad key", "/api/thread/archive", {"key": "x", "folder": "INBOX"})
