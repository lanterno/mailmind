"""The Mailbox's routes on their router: what the schemas refuse and what they let by, what a path and a query may name, and what each
use case is handed. What a route answers once it runs is the goldens' and each use case's own tests'."""
import base64
import urllib.parse

import pytest

import problems
from fixtures import world as w
from mailmind.bootstrap import assistant, mailbox
from mailmind.bootstrap.mailbox import store

BAD_REQUEST = problems.BAD_REQUEST
NOT_A_ROUTE = (404, problems.NO_ROUTE)
RECEIPT = "ada-inbox-05-receipt.eml"
PAST_2_53 = 2 ** 53 + 1
PDF = b"%PDF-1.7 fictional"
WITH_A_FILE = b"""From: Bo Lin <bo@example.test>
To: Ada Park <ada@example.test>
Subject: The contract
Date: Sun, 01 Nov 2026 09:00:00 +0000
Message-ID: <contract@example.test>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="b"

--b
Content-Type: text/plain; charset=utf-8

Attached.
--b
Content-Type: Application/PDF; name="ignored.pdf"
Content-Disposition: attachment; filename="../../caf\xc3\xa9 \\"final\\".pdf"
Content-Transfer-Encoding: base64

%s
--b
Content-Type: text/html
Content-Disposition: attachment; filename="page.html"

<script>parent.pwned=1</script>
--b
Content-Type: application/x-unnamed
Content-Disposition: attachment

--b--
""" % base64.b64encode(PDF)


def row_of(pk):
    c = store.conn()
    try:
        return c.execute("SELECT * FROM messages WHERE pk=?", (pk,)).fetchone()
    finally:
        c.close()


def delivered(world, raw):
    """The copy of `raw` delivered to Ada's Inbox, as a request names it."""
    uid = world.deliver(w.ADA, "INBOX", raw)
    c = store.conn()
    try:
        pk = c.execute("SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND uid=?", (w.ADA, uid)).fetchone()["pk"]
    finally:
        c.close()
    return w.copy_of(pk)


# ── what a request may say ───────────────────────────────────────────────────────────────────────────────────────────

COPY = {"key": "KEY", "folder": "FOLDER", "uid": "UID"}   # the receipt's copy, as `named` fills it in


def named(said, world):
    """A request's body with the receipt's key, folder and uid where it says KEY, FOLDER and UID, and its row's pk where it
    says PK."""
    if isinstance(said, dict):
        return {k: named(v, world) for k, v in said.items()}
    if isinstance(said, list | tuple):
        return type(said)(named(v, world) for v in said)
    if said in ("KEY", "FOLDER", "UID", "PK"):
        return {"KEY": world.key(RECEIPT), "FOLDER": world.copy(RECEIPT)["folder"], "UID": world.uid(RECEIPT),
                "PK": world.pk(RECEIPT)}[said]
    return said


@pytest.mark.parametrize("path, body", [
    ("/api/archive", {"key": "x", "folder": "FOLDER"}),
    ("/api/junk", {"key": [1], "folder": "FOLDER"}),
    ("/api/seen", {"key": PAST_2_53, "folder": "FOLDER"}),
    ("/api/thread/archive", {"key": {"a": 1}, "folder": "FOLDER"}),
    ("/api/trash", {"key": "KEY", "folder": 5}),
    ("/api/flag", {"key": "KEY", "folder": None}),
    ("/api/seen", {**COPY, "seen": "no"}),
    ("/api/seen", {**COPY, "seen": 0}),
    ("/api/flag", {**COPY, "on": "no"}),
    ("/api/flag", {**COPY, "flag": 5}),
    ("/api/trash", {**COPY, "undo_group": 5}),
    ("/api/archive", {**COPY, "mutation_id": 5}),
    ("/api/thread/trash", {**COPY, "mutation_id": ["m"]}),
    ("/api/bulk/file", {"verb": "archive", "rows": [{**COPY, "whole": "yes"}]}),
    ("/api/bulk/file", {"verb": "archive", "rows": [COPY], "undo_group": 5}),
    ("/api/reconcile", {"dry_run": "no"}),
    ("/api/sync-retry-account", {"account": 5}),
    ("/api/pending/retry", {"id": "x"}),
    ("/api/pending/discard", {"id": PAST_2_53}),
])
def test_a_value_its_model_refuses_is_the_400_and_nothing_happens(app, world, path, body):
    pk = world.pk(RECEIPT)
    mark = world.mark()
    r = app.post(path, named(body, world))
    assert (r.status, r.json) == (400, BAD_REQUEST)
    assert world.writes(mark) == []
    assert row_of(pk)["pending_removal"] is None and not row_of(pk)["seen"] and not row_of(pk)["flagged"]


@pytest.mark.parametrize("path", ["/api/trash", "/api/archive", "/api/junk", "/api/seen", "/api/flag", "/api/thread/trash"])
@pytest.mark.parametrize("field", ["subject", "text", "account", "thread_id", "pk"])
def test_a_field_nothing_here_reads_is_nothing_to_refuse(app, world, path, field):
    r = app.post(path, {**world.copy(RECEIPT), field: 5})
    assert (r.status, r.json["ok"]) == (200, True)


@pytest.mark.parametrize("path", ["/api/undo", "/api/refresh"])
def test_a_route_that_reads_no_body_still_wants_an_object(app, path):
    done = 404 if path == "/api/undo" else 200            # read as {}: with nothing to undo, Undo says so
    assert app.post(path, b"[]").status == 400
    assert app.post(path, b"not json").status == 400
    assert app.post(path, {"subject": 5}).status == done
    assert app.post(path, b"").status == done


@pytest.mark.parametrize("path, status, answer", [
    ("/api/archive", 400, BAD_REQUEST),                 # a copy's key and folder are the schema's to require
    ("/api/junk", 400, BAD_REQUEST),
    ("/api/seen", 400, BAD_REQUEST),
    ("/api/flag", 400, BAD_REQUEST),
    ("/api/thread/archive", 400, BAD_REQUEST),
    ("/api/thread/trash", 400, BAD_REQUEST),
    ("/api/trash", 400, BAD_REQUEST),
    ("/api/bulk/file", 400, BAD_REQUEST),
    ("/api/pending/retry", 400, BAD_REQUEST),
    ("/api/pending/discard", 400, BAD_REQUEST),
    ("/api/sync-retry-account", 400, problems.refused("unknown account")),
])
def test_what_is_left_out_stays_the_use_cases_to_word(app, path, status, answer):
    r = app.post(path, {})
    assert (r.status, r.json) == (status, answer)
    r = app.post(path, b"")
    assert (r.status, r.json) == (status, answer)


def test_what_is_left_out_does_what_it_always_did(app, world):
    pk, copy = world.pk(RECEIPT), world.copy(RECEIPT)
    app.post("/api/seen", {**copy, "seen": False})
    assert not row_of(pk)["seen"]
    assert app.post("/api/seen", copy).json["ok"]
    assert row_of(pk)["seen"]
    assert app.post("/api/seen", {**copy, "seen": None}).json["ok"]       # a null has always read as false
    assert not row_of(pk)["seen"]
    assert app.post("/api/flag", copy).json["ok"]
    assert row_of(pk)["flagged"]
    assert app.post("/api/flag", {**copy, "on": False}).json["ok"]
    assert not row_of(pk)["flagged"]
    r = app.post("/api/flag", {**copy, "flag": None})
    assert (r.status, r.json) == (400, problems.refused("unknown flag"))
    r = app.post("/api/flag", {**copy, "flag": "deleted"})               # no flag a request may set: the schema's
    assert (r.status, r.json) == (400, BAD_REQUEST)


def test_a_number_may_still_come_as_text(app, world):
    copy = world.copy(RECEIPT)
    r = app.post("/api/seen", {**copy, "key": str(copy["key"])})
    assert (r.status, r.json["ok"]) == (200, True)
    r = app.post("/api/bulk/file", {"verb": "archive", "rows": [{**copy, "key": str(copy["key"])}]})
    assert (r.status, r.json["count"]) == (200, 1)


# ── what each use case is handed ─────────────────────────────────────────────────────────────────────────────────────

FILED = {"ok": True, "msg": "ok", "undoable": False}
MANY = {"ok": True, "msg": "filed", "filed": [{"key": 9, "folder": "INBOX", "uid": 4}], "count": 1, "failed": 0, "queued": 0, "undoable": True}
EACH_SAYS = {**COPY, "undo_group": "g", "mutation_id": "m"}
HANDED = {**EACH_SAYS, "pk": "PK"}                     # with the pk the copy has now, which is how a use case reads it


@pytest.mark.parametrize("use_case, answer, path, body, handed", [
    ("do_trash", FILED, "/api/trash", EACH_SAYS, (HANDED,)),
    ("do_write", FILED, "/api/archive", EACH_SAYS, ("/api/archive", HANDED)),
    ("do_write", FILED, "/api/junk", EACH_SAYS, ("/api/junk", HANDED)),
    ("do_write", FILED, "/api/seen", {**COPY, "seen": False, "mutation_id": "m"},
     ("/api/seen", {**COPY, "pk": "PK", "seen": False, "mutation_id": "m"})),
    ("do_write", FILED, "/api/flag", {**COPY, "flag": "seen", "on": False, "mutation_id": "m"},
     ("/api/flag", {**COPY, "pk": "PK", "flag": "seen", "on": False, "mutation_id": "m"})),
    ("do_write", FILED, "/api/seen", COPY, ("/api/seen", {**COPY, "pk": "PK"})),      # what wasn't said isn't made up
    ("do_write", FILED, "/api/seen", {**COPY, "seen": None}, ("/api/seen", {**COPY, "pk": "PK", "seen": None})),
    ("do_thread_file", MANY, "/api/thread/archive", EACH_SAYS, ("/api/thread/archive", HANDED)),
    ("do_thread_file", MANY, "/api/thread/trash", EACH_SAYS, ("/api/thread/trash", HANDED)),
    ("file_many", {**MANY, "rows_failed": 0, "partial": False}, "/api/bulk/file",
     {"verb": "archive", "rows": [{"key": 5, "folder": "INBOX", "uid": 3, "whole": True}, {"key": 6, "folder": "Archive"}],
      "undo_group": "g", "mutation_id": "m"},
     ({"verb": "archive", "rows": [{"key": 5, "folder": "INBOX", "uid": 3, "whole": True}, {"key": 6, "folder": "Archive"}],
       "undo_group": "g", "mutation_id": "m"},)),
    ("do_pending_retry", {"ok": True}, "/api/pending/retry", {"id": "7"}, (7,)),
    ("do_pending_discard", {"ok": True}, "/api/pending/discard", {"id": 7}, (7,)),
])
def test_filing_is_handed_all_the_request_said_and_nothing_else(app, world, monkeypatch, use_case, answer, path, body, handed):
    got = []

    def spy(*args):
        got.append(args)
        return answer
    monkeypatch.setattr(mailbox.filing, use_case, spy)
    r = app.post(path, {**named(body, world), "ignored": "by every model"})
    assert (r.status, r.json) == (200, answer)
    assert got == [named(handed, world)]


@pytest.mark.parametrize("body, handed", [
    ({}, ("all", False)),
    ({"account": None, "dry_run": None}, (None, False)),
    ({"account": w.ADA, "dry_run": True}, (w.ADA, True)),
    ({"account": w.ADA, "force": True}, (w.ADA, False)),
])
def test_a_reconcile_is_handed_what_it_always_was(app, monkeypatch, body, handed):
    got = []

    def spy(account, dry_run):
        got.append((account, dry_run))
        return {"ok": True, "dry_run": dry_run, "results": [], "total_ghosts": 0, "total_pruned": 0, "generated": "now"}
    monkeypatch.setattr(mailbox.reconciler, "do_reconcile", spy)
    assert app.post("/api/reconcile", body).status == 200
    assert got == [handed]


@pytest.mark.parametrize("query, handed", [
    ("", (None, "INBOX", 0, 80, False)),
    ("?account=a&folder=Sent&offset=2&limit=3&group=thread", ("a", "Sent", 2, 3, True)),
    ("?folder=&limit=&offset=&group=", (None, "INBOX", 0, 80, False)),         # a blank is no value at all
    ("?limit=2&limit=x&group=other", (None, "INBOX", 0, 2, False)),            # a name's first value is the one read
])
def test_a_list_is_handed_what_its_query_said(app, monkeypatch, query, handed):
    got = []

    def spy(account, folder, offset, limit, group=False):
        got.append((account, folder, offset, limit, group))
        return {"ok": True, "account": account, "folder": folder, "items": [], "grouped": group, "total": 0, "offset": offset, "has_more": False}
    monkeypatch.setattr(mailbox.queries, "messages_in", spy)
    assert app.get(f"/api/messages{query}").status == 200
    assert got == [handed]


# ── what a query may name ────────────────────────────────────────────────────────────────────────────────────────────

def opened(app, copy):
    return app.get("/api/message/body?" + urllib.parse.urlencode(copy))


def part_of(app, copy, part_id):
    return app.get("/api/message/attachment?" + urllib.parse.urlencode({**copy, "part_id": part_id}))


@pytest.mark.parametrize("path", ["/api/message/body?key={}&folder=INBOX", "/api/message/attachment?key={}&folder=INBOX&part_id=1"])
@pytest.mark.parametrize("junk", ["x", "1.5", "1%2F2", str(PAST_2_53)])
def test_a_key_that_wont_read_is_the_400(app, path, junk):
    r = app.get(path.format(junk))
    assert (r.status, r.json) == (400, BAD_REQUEST)


@pytest.mark.parametrize("path", ["/api/message/body?key={}&folder=INBOX", "/api/message/attachment?key={}&folder=INBOX&part_id=1"])
@pytest.mark.parametrize("key", [2 ** 53, 0, -1])
def test_a_key_that_reads_and_names_nothing_is_no_message(app, path, key):
    r = app.get(path.format(key))
    assert (r.status, r.json) == (404, problems.not_found("not found"))


@pytest.mark.parametrize("part", ["1x", "1.0", "-1", "%D9%A3", "x", "1%0A", "1%0D%0A", "%0A1"])
def test_a_part_id_that_isnt_one_is_refused(app, world, part):
    r = app.get("/api/message/attachment?key={key}&folder={folder}&part_id=".format(**world.copy(RECEIPT)) + part)
    assert (r.status, r.json) == (400, problems.refused("bad part id"))


def test_a_bad_key_is_said_before_a_bad_part_id(app):
    assert app.get("/api/message/attachment?key=x&folder=INBOX&part_id=y").json == BAD_REQUEST


@pytest.mark.parametrize("path", ["/api/message/1/body", "/api/message/1/attachment/1", "/api/message/1/body.html",   # a pk names nothing
                                  f"/api/message/{'9' * 5000}/body.html", "/api/message/body/", "/api/message//body",
                                  "/api/message/attachment/1", "/api/message/1/body/", "/api/message/body.html/1"])
def test_a_path_of_any_other_shape_is_no_route(app, path):
    r = app.get(path)
    assert (r.status, r.json) == NOT_A_ROUTE


# ── a message's file ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_file_comes_as_a_download_under_a_policy_that_runs_nothing(app, world):
    copy = delivered(world, WITH_A_FILE)
    parts = {a["filename"]: a["part_id"] for a in opened(app, copy).json["attachments"]}
    r = part_of(app, copy, parts['../../café "final".pdf'])
    assert (r.status, r.body) == (200, PDF)
    assert r.headers["content-type"] == "application/pdf"          # as the part says, lower case, its parameters gone
    assert r.headers["content-disposition"] == "attachment; filename*=UTF-8''caf%C3%A9%20_final_.pdf"
    assert r.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert r.headers["content-length"] == str(len(PDF))
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"


def test_an_active_type_keeps_its_type_and_still_downloads(app, world):
    copy = delivered(world, WITH_A_FILE)
    parts = {a["filename"]: a["part_id"] for a in opened(app, copy).json["attachments"]}
    r = part_of(app, copy, parts["page.html"])
    assert r.status == 200 and b"<script>" in r.body
    assert r.headers["content-type"] == "text/html"
    assert r.headers["content-disposition"] == "attachment; filename*=UTF-8''page.html"
    assert r.headers["content-security-policy"] == "default-src 'none'; sandbox"


def test_a_file_past_what_may_go_is_the_413(app, world, monkeypatch):
    copy = delivered(world, WITH_A_FILE)
    from mailmind.http.routers import mailbox as routes
    parts = {a["filename"]: a["part_id"] for a in opened(app, copy).json["attachments"]}
    monkeypatch.setattr(routes, "MAX_ATTACH_BYTES", len(PDF) - 1)
    r = part_of(app, copy, parts['../../café "final".pdf'])
    assert (r.status, r.json) == (413, problems.blank(413, "too large"))


def test_no_such_part_is_the_404(app, world):
    copy = delivered(world, WITH_A_FILE)
    r = part_of(app, copy, "99")
    assert (r.status, r.json) == (404, problems.not_found("no such part"))
    r = part_of(app, {"key": 999999, "folder": "INBOX"}, "1")
    assert (r.status, r.json) == (404, problems.not_found("not found"))


# ── a frame's document ───────────────────────────────────────────────────────────────────────────────────────────────

def is_a_document(r):
    assert r.headers["content-type"] == "text/html; charset=utf-8"
    assert r.headers["x-frame-options"] == "SAMEORIGIN" and "sandbox" in r.headers["content-security-policy"]
    assert r.body.startswith(b"<!doctype html>")
    return True


@pytest.mark.parametrize("query", ["key=%FF&folder=INBOX", "key=%00&folder=INBOX", "key=" + "9" * 3000 + "&folder=INBOX", "images=abc", "images=",
                                   "images=%FF", "key=a&key=b&images=1&images=0", "key=[1]&folder=INBOX", "key[]=1", "unknown=1",
                                   "key=99999999999999999999999999&folder=INBOX", "key=KEY", "folder=INBOX", "key=KEY&folder=%FF",
                                   "key=KEY&folder=INBOX%00", "key=KEY&folder=[INBOX]", "key=KEY&folder[]=INBOX"])
def test_nothing_in_a_documents_query_is_refused(app, world, query):
    r = app.get("/api/message/body.html?" + query.replace("KEY", str(world.key(RECEIPT))))
    assert r.status == 404 and is_a_document(r)


def test_a_documents_query_reads_each_names_first_value(app, world):
    key = world.key(RECEIPT)
    assert app.get(f"/api/message/body.html?key={key}&key=zzz&folder=INBOX").status == 200
    assert app.get(f"/api/message/body.html?key=zzz&key={key}&folder=INBOX").status == 404
    assert app.get(f"/api/message/body.html?key={key}&folder=INBOX&folder=Archive").status == 200
    assert app.get(f"/api/message/body.html?key={key}&folder=Archive&folder=INBOX").status == 404
    with_images = app.get(f"/api/message/body.html?key={key}&folder=INBOX&images=1&images=0")
    assert "img-src data: https:" in with_images.headers["content-security-policy"]


# ── what finds nothing, and who counts as here ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path, said", [("/api/thread", problems.refused("missing account/thread")),
                                        ("/api/thread?account=&thread_id=", problems.refused("missing account/thread")),
                                        ("/api/message/body?key=999999&folder=INBOX", problems.not_found("not found")),
                                        ("/api/message/body?key=999999&folder=INBOX&prefetch=1", problems.not_found("not found"))])
def test_a_get_that_finds_nothing_says_so(app, path, said):
    r = app.get(path)
    assert (r.status, r.json) == (said["status"], said)


def test_folders_with_no_account_named_say_so_with_an_empty_list(app):
    for path in ("/api/folders", "/api/folders?account="):
        r = app.get(path)
        assert (r.status, r.json) == (400, problems.refused("account required", folders=[]))


def test_undo_with_nothing_to_undo_says_so(app):
    r = app.post("/api/undo", {})
    assert (r.status, r.json) == (404, problems.not_found(empty=True))


@pytest.mark.parametrize("path, counts", [("/api/digest", True), ("/api/inboxes", True), ("/api/message/body?key=999999&folder=INBOX", True),
                                          ("/api/message/attachment?key=999999&folder=INBOX&part_id=1", True),
                                          ("/api/message/body.html?key=999999&folder=INBOX", True),
                                          ("/api/folders", False), ("/api/pending", False), ("/api/stats", False)])
def test_who_counts_as_someone_here(app, monkeypatch, path, counts):
    """What only a tab's own listeners ask doesn't: the rail, the tray and the counts."""
    seen = []
    monkeypatch.setattr(assistant.governor(), "saw_someone", lambda: seen.append(1))
    app.get(path)
    assert bool(seen) is counts


def test_a_refused_request_counts_as_no_one(app, monkeypatch):
    seen = []
    monkeypatch.setattr(assistant.governor(), "saw_someone", lambda: seen.append(1))
    assert app.post("/api/archive", {"key": 1, "folder": "INBOX"}, headers={"X-CSRF-Token": "wrong"}).status == 403
    assert app.post("/api/archive", {"key": [1], "folder": "INBOX"}).status == 400
    assert app.get("/api/message/body?key=x&folder=INBOX").status == 400
    assert seen == []
