"""The Mailbox's routes name a message by its key and a copy by its key, folder and uid, which outlast a row's pk: a renumber, a
move and its Undo give the rows new pks, and the same key and folder still name the same message (mailmind.http.named). Two
copies in one folder can share a key, and only the uid tells them apart."""
from urllib.parse import urlencode

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
    assert world.copy(LUNCH) in [{"key": it["key"], "folder": it["folder"], "uid": it["uid"]} for it in lunch]


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
    by_uid = app.get("/api/message/attachment?" + urlencode({**world.copy(INVITE), "part_id": part}))
    assert by_uid.status == 200 and by_uid.body == r.body
    elsewhere = app.get("/api/message/attachment?" + urlencode({**world.copy(INVITE), "uid": world.uid(INVITE) + 100, "part_id": part}))
    assert elsewhere.status == 404


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

OTHER = "Not what you read"


def a_second_copy(world, name=LUNCH):
    """Someone who knows a message's Message-ID sends another under it: a second copy in its folder, under its key, with a
    subject of its own. The newer, so a rule that took the newer would take it."""
    uid = world.deliver(w.ADA, "INBOX", w.variant(name, Subject=OTHER))
    return {"key": world.key(name), "folder": "INBOX", "uid": uid}


def opened_subject(app, copy):
    r = app.get("/api/message/body?" + urlencode(copy))
    return r.json["subject"] if r.status == 200 else r.status


def in_the_inbox(app, key):
    return sorted(it["uid"] for it in app.get(f"/api/messages?account={w.ADA}&folder=INBOX").json["items"] if it["key"] == key)


def renumber_afresh(world, account=w.ADA):
    """A new UIDVALIDITY under which no message in the Inbox has the uid it had, and synced."""
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    world.server.renumber(account, "INBOX", first=500)
    ingest.sync_new(accounts.get(account))


def test_two_copies_in_one_folder_under_one_key_each_open_by_their_uid(app, world):
    real, other = world.copy(LUNCH), a_second_copy(world)
    assert real["key"] == other["key"] and real["uid"] != other["uid"]
    assert in_the_inbox(app, real["key"]) == sorted([real["uid"], other["uid"]])     # each a row of its own
    assert opened_subject(app, real) == world.row(LUNCH)["subject"]
    assert opened_subject(app, other) == OTHER
    opened = app.get("/api/message/body?" + urlencode(real)).json
    assert (opened["key"], opened["folder"], opened["uid"]) == (real["key"], "INBOX", real["uid"])


def test_filing_one_of_two_copies_files_the_one_named(app, world):
    real, other = world.copy(LUNCH), a_second_copy(world)
    r = app.post("/api/archive", real).json
    assert r["ok"] is True
    assert in_the_inbox(app, real["key"]) == [other["uid"]]
    on_the_server = world.server.messages(w.ADA, "INBOX")
    assert real["uid"] not in on_the_server and on_the_server[other["uid"]].headers()["Subject"] == OTHER


def test_a_star_and_a_marked_row_go_to_the_one_of_two_copies_named(app, world):
    real, other = world.copy(LUNCH), a_second_copy(world)
    app.post("/api/flag", {**other, "flag": "flagged", "on": True})
    flagged = {r["uid"]: r["flagged"] for r in q("SELECT uid, flagged FROM messages WHERE message_key=? AND folder='INBOX'", real["key"])}
    assert flagged == {real["uid"]: 0, other["uid"]: 1}
    r = app.post("/api/bulk/file", {"verb": "archive", "rows": [other]}).json
    assert (r["count"], r["filed"]) == (1, [other])
    assert in_the_inbox(app, real["key"]) == [real["uid"]]


def test_without_a_uid_a_key_and_folder_name_neither_of_two_copies(app, world):
    """Rather than a guess at one of them, which is what someone who sent the second would want."""
    real = world.copy(LUNCH)
    a_second_copy(world)
    bare = {"key": real["key"], "folder": "INBOX"}
    since = world.mark()
    assert opened_subject(app, bare) == 404
    for path in ("/api/archive", "/api/trash", "/api/seen", "/api/flag", "/api/thread/archive"):
        r = app.post(path, {**bare, "seen": True, "flag": "flagged", "on": True})
        assert (r.status, r.json) == (404, problems.not_found("not found")), path
    r = app.post("/api/bulk/file", {"verb": "archive", "rows": [bare]})
    assert r.status == 404
    assert world.writes(since) == []


def test_without_a_uid_the_only_copy_in_a_folder_is_the_one_named(app, world):
    copy = world.copy(RECEIPT)
    assert opened_subject(app, {"key": copy["key"], "folder": "INBOX"}) == world.row(RECEIPT)["subject"]


def test_a_stale_uid_names_nothing_though_one_copy_is_left(app, world):
    """A link kept across a new UIDVALIDITY: the copy it named has another uid now, and it opens nothing rather than the copy
    a guess would take. The list's row, read again, names it."""
    copy, subject = world.copy(RECEIPT), world.row(RECEIPT)["subject"]
    renumber_afresh(world)
    [now] = in_the_inbox(app, copy["key"])
    assert now != copy["uid"]
    since = world.mark()
    assert opened_subject(app, copy) == 404
    assert app.post("/api/archive", copy).status == 404
    assert world.writes(since) == []
    assert opened_subject(app, {**copy, "uid": now}) == subject


def test_a_stale_uid_names_neither_of_two_copies(app, world):
    real = world.copy(LUNCH)
    a_second_copy(world)
    renumber_afresh(world)
    assert len(in_the_inbox(app, real["key"])) == 2 and real["uid"] not in in_the_inbox(app, real["key"])
    since = world.mark()
    assert opened_subject(app, real) == 404
    assert app.post("/api/archive", real).status == 404
    assert world.writes(since) == []


def test_a_copy_on_its_way_out_is_named_by_its_uid(app, world):
    """A move still queued takes its row off every list. Alone in its folder it is still the copy there; beside a new one,
    each has its uid."""
    from mailmind.bootstrap import mailbox
    key = world.key(LUNCH)
    world.queued_move(LUNCH, "Archive")
    assert mailbox.queries.pk_of_copy(key, "INBOX") == world.pk(LUNCH)     # the only one there, on its way out
    uid = world.deliver(w.ADA, "INBOX", w.raw(LUNCH))
    [listed] = q("SELECT pk FROM messages WHERE message_key=? AND pending_removal IS NULL", key)
    assert mailbox.queries.pk_of_copy(key, "INBOX") is None
    assert mailbox.queries.pk_of_copy(key, "INBOX", uid) == listed["pk"]
    assert mailbox.queries.pk_of_copy(key, "INBOX", world.uid(LUNCH)) == world.pk(LUNCH)


def test_a_draft_reopens_from_the_one_of_two_copies_its_uid_names(app, world):
    """Thunderbird saves a draft again under its Message-ID: each copy in Drafts reopens as itself, and with no uid neither."""
    texts = {}
    for name in (DRAFT_V1, DRAFT_V2):
        r = app.post("/api/draft/resume", world.copy(name))
        assert r.status == 200, r.body
        texts[name] = r.json["text"]
    assert texts[DRAFT_V1] != texts[DRAFT_V2]
    bare = app.post("/api/draft/resume", {"key": world.key(DRAFT_V1), "folder": "Drafts"})
    assert (bare.status, bare.json) == (404, problems.not_found("That draft is no longer in Drafts."))


def test_a_route_for_the_message_takes_the_copy_its_uid_names(app, world):
    """Thunderbird saves a draft again under its Message-ID: both are in Drafts under one key. A summary or a reply takes the
    copy whose uid it was given, and with none, or one that names neither, the newer."""
    from mailmind.bootstrap import mailbox
    key = world.key(DRAFT_V1)
    assert key == world.key(DRAFT_V2)
    for name in (DRAFT_V1, DRAFT_V2):
        assert mailbox.queries.pk_of_message(key, "Drafts", world.uid(name)) == world.pk(name)
    assert mailbox.queries.pk_of_message(key, "Drafts") == world.pk(DRAFT_V2)
    assert mailbox.queries.pk_of_message(key, "Drafts", 999_999) == world.pk(DRAFT_V2)
