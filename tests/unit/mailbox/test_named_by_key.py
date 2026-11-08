"""The Mailbox's routes name a message by its key and a copy by its key and folder, which outlast a row's pk: a renumber, a move
and its Undo give the rows new pks, and the same key and folder still name the same message (mailmind.http.named)."""
import problems
from fixtures import world as w
from mailmind.bootstrap.mailbox import store

LUNCH = "ada-inbox-01-lunch.eml"
CONFIRM = "ada-inbox-02-lunch-confirm.eml"
RECEIPT = "ada-inbox-05-receipt.eml"
INVITE = "ada-inbox-06-outlook-invite.eml"
DRAFT_V1 = "ada-drafts-01-thunderbird-v1.eml"
DRAFT_V2 = "ada-drafts-02-thunderbird-v2.eml"


def q(sql, *params):
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, params)]
    finally:
        c.close()


def renumber_inbox(world, account=w.ADA):
    """Every UID in the Inbox renumbered, and synced: each row the Inbox had is gone, and each message has a new one."""
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    world.server.renumber(account, "INBOX")
    ingest.sync_new(accounts.get(account))


def boxes(world, name):
    return [box for _, box, _, _ in world.where(name)["server"]]


def subject_of(answer):
    return answer.json["subject"]


# ── what a list names ────────────────────────────────────────────────────────────────────────────────────────────────

def test_each_row_of_a_list_names_its_copy(app, world):
    for path in (f"/api/messages?account={w.ADA}&folder=INBOX", f"/api/messages?account={w.ADA}&folder=INBOX&group=thread",
                 "/api/inboxes", "/api/inboxes?group=thread"):
        items = app.get(path).json["items"]
        assert items and all(isinstance(it["key"], int) and it["folder"] == "INBOX" for it in items), path
    lunch = app.get(f"/api/messages?account={w.ADA}&folder=INBOX").json["items"]
    assert world.copy(LUNCH) in [{"key": it["key"], "folder": it["folder"]} for it in lunch]


def test_a_conversation_says_which_message_a_reply_answers(app, world):
    row = world.row(CONFIRM)
    t = app.get(f"/api/thread?account={w.ADA}&thread_id={row['thread_id']}").json
    assert t["reply_key"] == t["items"][-1]["key"]
    assert all({"key", "folder"} <= set(it) for it in t["items"])


# ── a message opened ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_copy_opens_by_its_key_and_folder(app, world):
    opened = app.get("/api/message/body?key={key}&folder={folder}".format(**world.copy(LUNCH))).json
    assert (opened["key"], opened["folder"], opened["subject"]) == (world.key(LUNCH), "INBOX", world.row(LUNCH)["subject"])


def test_a_kept_key_opens_the_same_message_after_a_renumber(app, world):
    copy, pk, subject = world.copy(LUNCH), world.pk(LUNCH), world.row(LUNCH)["subject"]
    renumber_inbox(world)
    assert pk not in [r["pk"] for r in q("SELECT pk FROM messages WHERE message_key=?", copy["key"])]
    assert subject_of(app.get("/api/message/body?key={key}&folder={folder}".format(**copy))) == subject


def test_a_copy_that_isnt_there_opens_nothing(app, world):
    key = world.key(LUNCH)
    assert app.get(f"/api/message/body?key={key}&folder=Archive").status == 404
    assert app.get("/api/message/body?key=987654&folder=INBOX").status == 404
    for missing in (f"/api/message/body?key={key}", "/api/message/body?folder=INBOX", "/api/message/body?key=x&folder=INBOX"):
        r = app.get(missing)
        assert (r.status, r.json) == (400, problems.BAD_REQUEST), missing


def test_a_prefetch_names_the_copy_too(app, world):
    r = app.get("/api/message/body?key={key}&folder={folder}&prefetch=1".format(**world.copy(RECEIPT))).json
    assert r == {"ok": True, "prefetched": True}


def test_the_frame_loads_a_copys_document(app, world):
    copy = world.copy(LUNCH)
    r = app.get("/api/message/body.html?key={key}&folder={folder}&images=0".format(**copy))
    assert r.status == 200 and r.headers["content-type"].startswith("text/html")
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]


def test_a_frame_for_a_copy_that_isnt_there_is_a_document_saying_so(app, world):
    key = world.key(LUNCH)
    for path in (f"/api/message/body.html?key={key}&folder=Archive", "/api/message/body.html?key=987654&folder=INBOX",
                 "/api/message/body.html?key=x&folder=INBOX", "/api/message/body.html", "/api/message/body.html?key=%EF%BC%91&folder=INBOX"):
        r = app.get(path)
        assert r.status == 404 and r.headers["content-type"].startswith("text/html"), path
        assert b"here any more" in r.body


def test_an_attachment_comes_from_the_copy_named(app, world):
    opened = app.get("/api/message/body?key={key}&folder={folder}".format(**world.copy(INVITE))).json
    part = opened["invite_part"]
    r = app.get("/api/message/attachment?key={key}&folder={folder}&part_id={part}".format(**world.copy(INVITE), part=part))
    assert r.status == 200 and b"BEGIN:VCALENDAR" in r.body
    assert app.get(f"/api/message/attachment?key={world.key(INVITE)}&folder=Archive&part_id={part}").status == 404


# ── filing ───────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_copy_is_filed_by_its_key_and_folder(app, world):
    r = app.post("/api/archive", world.copy(RECEIPT)).json
    assert r["ok"] is True and boxes(world, RECEIPT) == ["Archive"]


def test_a_kept_key_files_the_same_message_after_a_renumber(app, world):
    copy = world.copy(RECEIPT)
    renumber_inbox(world)
    assert app.post("/api/trash", copy).json["ok"] is True
    assert boxes(world, RECEIPT) == ["Trash"] and boxes(world, LUNCH) == ["INBOX"]


def test_filing_a_copy_that_has_gone_does_nothing(app, world):
    key = world.key(RECEIPT)
    since = world.mark()
    for path in ("/api/archive", "/api/trash", "/api/junk", "/api/seen", "/api/flag", "/api/thread/archive"):
        r = app.post(path, {"key": key, "folder": "Archive"})
        assert (r.status, r.json) == (404, problems.not_found("not found")), path
    assert world.writes(since) == []


def test_a_star_and_a_read_mark_go_to_the_copy_named(app, world):
    app.post("/api/flag", {**world.copy(RECEIPT), "flag": "flagged", "on": True})
    app.post("/api/seen", {**world.copy(LUNCH), "seen": True})
    assert world.row(RECEIPT)["flagged"] == 1 and world.row(LUNCH)["seen"] == 1


def test_a_conversation_is_filed_by_a_copy_of_it_and_says_what_left(app, world):
    r = app.post("/api/thread/archive", world.copy(CONFIRM)).json
    assert sorted(r["filed"], key=lambda x: x["key"]) == sorted([world.copy(LUNCH), world.copy(CONFIRM)], key=lambda x: x["key"])
    assert boxes(world, LUNCH) == ["Archive"] and boxes(world, CONFIRM) == ["Archive"]


def test_marked_rows_go_by_their_copies_and_one_gone_counts_as_failed(app, world):
    gone = {"key": world.key(LUNCH), "folder": "Archive"}
    r = app.post("/api/bulk/file", {"verb": "archive", "rows": [world.copy(RECEIPT), gone]}).json
    assert (r["count"], r["rows_failed"], r["filed"]) == (1, 1, [world.copy(RECEIPT)])
    assert boxes(world, RECEIPT) == ["Archive"] and boxes(world, LUNCH) == ["INBOX"]


def test_a_marked_row_names_a_copy_whole(app, world):
    for row in ({"key": world.key(RECEIPT)}, {"folder": "INBOX", "key": "x"}, {"key": world.key(RECEIPT), "folder": 5}):
        r = app.post("/api/bulk/file", {"verb": "archive", "rows": [row]})
        assert (r.status, r.json) == (400, problems.BAD_REQUEST), row


# ── which copy ───────────────────────────────────────────────────────────────────────────────────────────────────────

def test_of_two_copies_in_one_folder_the_newer_is_the_one_named(app, world):
    """Thunderbird saves a draft again under its Message-ID: both are in Drafts, and one key and folder name them."""
    assert world.key(DRAFT_V1) == world.key(DRAFT_V2)
    opened = app.get("/api/message/body?key={key}&folder={folder}".format(**world.copy(DRAFT_V1))).json
    assert opened["subject"] == world.row(DRAFT_V2)["subject"]


def test_a_copy_on_its_way_out_is_named_last(app, world):
    """A move still queued takes its row off every list, and its key and folder name the copy that is listed, if any."""
    from mailmind.bootstrap import mailbox
    key = world.key(LUNCH)
    world.queued_move(LUNCH, "Archive")
    assert mailbox.queries.pk_of_copy(key, "INBOX") == world.pk(LUNCH)     # the only one there, on its way out
    world.deliver(w.ADA, "INBOX", w.raw(LUNCH))
    listed = q("SELECT pk FROM messages WHERE message_key=? AND pending_removal IS NULL", key)
    assert [mailbox.queries.pk_of_copy(key, "INBOX")] == [r["pk"] for r in listed]
