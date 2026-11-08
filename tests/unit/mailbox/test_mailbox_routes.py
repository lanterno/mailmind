"""The Mailbox's routes on their router: what the schemas refuse and what they let by, what a path may name, and what each use case
is handed. What a route answers once it runs is the goldens' and each use case's own tests'."""
import base64

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
    uid = world.deliver(w.ADA, "INBOX", raw)
    c = store.conn()
    try:
        return c.execute("SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND uid=?", (w.ADA, uid)).fetchone()["pk"]
    finally:
        c.close()


# ── what a request may say ───────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path, body", [
    ("/api/archive", {"pk": "x"}),
    ("/api/junk", {"pk": [1]}),
    ("/api/seen", {"pk": PAST_2_53}),
    ("/api/thread/archive", {"pk": {"a": 1}}),
    ("/api/seen", {"pk": "PK", "seen": "no"}),
    ("/api/seen", {"pk": "PK", "seen": 0}),
    ("/api/flag", {"pk": "PK", "on": "no"}),
    ("/api/flag", {"pk": "PK", "flag": 5}),
    ("/api/trash", {"pk": "PK", "undo_group": 5}),
    ("/api/archive", {"pk": "PK", "mutation_id": 5}),
    ("/api/thread/trash", {"pk": "PK", "mutation_id": ["m"]}),
    ("/api/bulk/file", {"verb": "archive", "rows": [{"pk": "PK", "whole": "yes"}]}),
    ("/api/bulk/file", {"verb": "archive", "rows": [{"pk": "PK"}], "undo_group": 5}),
    ("/api/reconcile", {"dry_run": "no"}),
    ("/api/reconcile", {"dry_run": True, "force": 1}),
    ("/api/sync-retry-account", {"account": 5}),
    ("/api/pending/retry", {"id": "x"}),
    ("/api/pending/discard", {"id": PAST_2_53}),
])
def test_a_value_its_model_refuses_is_the_400_and_nothing_happens(app, world, path, body):
    pk = world.pk(RECEIPT)

    def named(said):
        if isinstance(said, dict):
            return {k: named(v) for k, v in said.items()}
        if isinstance(said, list):
            return [named(v) for v in said]
        return pk if said == "PK" else said
    mark = world.mark()
    r = app.post(path, named(body))
    assert (r.status, r.json) == (400, BAD_REQUEST)
    assert world.writes(mark) == []
    assert row_of(pk)["pending_removal"] is None and not row_of(pk)["seen"] and not row_of(pk)["flagged"]


@pytest.mark.parametrize("path", ["/api/trash", "/api/archive", "/api/junk", "/api/seen", "/api/flag", "/api/thread/trash"])
@pytest.mark.parametrize("field", ["subject", "text", "account", "thread_id", "folder"])
def test_a_field_nothing_here_reads_is_nothing_to_refuse(app, world, path, field):
    r = app.post(path, {"pk": world.pk(RECEIPT), field: 5})
    assert (r.status, r.json["ok"]) == (200, True)


@pytest.mark.parametrize("path", ["/api/undo", "/api/refresh"])
def test_a_route_that_reads_no_body_still_wants_an_object(app, path):
    done = 404 if path == "/api/undo" else 200            # read as {}: with nothing to undo, Undo says so
    assert app.post(path, b"[]").status == 400
    assert app.post(path, b"not json").status == 400
    assert app.post(path, {"subject": 5}).status == done
    assert app.post(path, b"").status == done


@pytest.mark.parametrize("path, status, answer", [
    ("/api/archive", 400, problems.refused("bad pk")),
    ("/api/junk", 400, problems.refused("bad pk")),
    ("/api/seen", 400, problems.refused("bad pk")),
    ("/api/flag", 400, problems.refused("bad pk")),
    ("/api/thread/archive", 400, problems.refused("bad pk")),
    ("/api/thread/trash", 400, problems.refused("bad pk")),
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
    pk = world.pk(RECEIPT)
    app.post("/api/seen", {"pk": pk, "seen": False})
    assert not row_of(pk)["seen"]
    assert app.post("/api/seen", {"pk": pk}).json["ok"]
    assert row_of(pk)["seen"]
    assert app.post("/api/seen", {"pk": pk, "seen": None}).json["ok"]       # a null has always read as false
    assert not row_of(pk)["seen"]
    assert app.post("/api/flag", {"pk": pk}).json["ok"]
    assert row_of(pk)["flagged"]
    assert app.post("/api/flag", {"pk": pk, "on": False}).json["ok"]
    assert not row_of(pk)["flagged"]
    for flag in (None, "deleted"):
        r = app.post("/api/flag", {"pk": pk, "flag": flag})
        assert (r.status, r.json) == (400, problems.refused("unknown flag"))


def test_a_number_may_still_come_as_text(app, world):
    r = app.post("/api/seen", {"pk": str(world.pk(RECEIPT))})
    assert (r.status, r.json["ok"]) == (200, True)
    r = app.post("/api/bulk/file", {"verb": "archive", "rows": [{"pk": str(world.pk(RECEIPT))}]})
    assert (r.status, r.json["count"]) == (200, 1)


# ── what each use case is handed ─────────────────────────────────────────────────────────────────────────────────────

FILED = {"ok": True, "msg": "ok", "undoable": False}
MANY = {"ok": True, "msg": "filed", "pks": [5], "count": 1, "failed": 0, "queued": 0, "undoable": True}
EACH_SAYS = {"pk": 5, "undo_group": "g", "mutation_id": "m"}


@pytest.mark.parametrize("use_case, answer, path, body, handed", [
    ("do_trash", FILED, "/api/trash", EACH_SAYS, (EACH_SAYS,)),
    ("do_write", FILED, "/api/archive", EACH_SAYS, ("/api/archive", EACH_SAYS)),
    ("do_write", FILED, "/api/junk", EACH_SAYS, ("/api/junk", EACH_SAYS)),
    ("do_write", FILED, "/api/seen", {"pk": 5, "seen": False, "mutation_id": "m"}, ("/api/seen", {"pk": 5, "seen": False, "mutation_id": "m"})),
    ("do_write", FILED, "/api/flag", {"pk": 5, "flag": "seen", "on": False, "mutation_id": "m"}, ("/api/flag", {"pk": 5, "flag": "seen", "on": False, "mutation_id": "m"})),
    ("do_write", FILED, "/api/seen", {"pk": 5}, ("/api/seen", {"pk": 5})),      # what wasn't said isn't made up
    ("do_write", FILED, "/api/seen", {"pk": 5, "seen": None}, ("/api/seen", {"pk": 5, "seen": None})),
    ("do_thread_file", MANY, "/api/thread/archive", EACH_SAYS, ("/api/thread/archive", EACH_SAYS)),
    ("do_thread_file", MANY, "/api/thread/trash", EACH_SAYS, ("/api/thread/trash", EACH_SAYS)),
    ("file_many", {**MANY, "rows_failed": 0, "partial": False}, "/api/bulk/file",
     {"verb": "archive", "rows": [{"pk": 5, "whole": True}, {"pk": 6}], "undo_group": "g", "mutation_id": "m"},
     ({"verb": "archive", "rows": [{"pk": 5, "whole": True}, {"pk": 6}], "undo_group": "g", "mutation_id": "m"},)),
    ("do_pending_retry", {"ok": True}, "/api/pending/retry", {"id": "7"}, (7,)),
    ("do_pending_discard", {"ok": True}, "/api/pending/discard", {"id": 7}, (7,)),
])
def test_filing_is_handed_all_the_request_said_and_nothing_else(app, monkeypatch, use_case, answer, path, body, handed):
    got = []

    def spy(*args):
        got.append(args)
        return answer
    monkeypatch.setattr(mailbox.filing, use_case, spy)
    r = app.post(path, {**body, "ignored": "by every model"})
    assert (r.status, r.json) == (200, answer)
    assert got == [handed]


@pytest.mark.parametrize("body, handed", [
    ({}, ("all", False, False)),
    ({"account": None, "dry_run": None, "force": None}, (None, False, False)),
    ({"account": w.ADA, "dry_run": True, "force": True}, (w.ADA, True, True)),
])
def test_a_reconcile_is_handed_what_it_always_was(app, monkeypatch, body, handed):
    got = []

    def spy(account, dry_run, force=False):
        got.append((account, dry_run, force))
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


# ── what a path may name ─────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/api/message/{}/body", "/api/message/{}/attachment/1"])
@pytest.mark.parametrize("junk", ["x", "1.5", "%31", "1%2F2", str(PAST_2_53)])
def test_a_pk_that_wont_read_is_the_400(app, path, junk):
    r = app.get(path.format(junk))
    assert (r.status, r.json) == (400, BAD_REQUEST)


@pytest.mark.parametrize("path", ["/api/message/{}/body", "/api/message/{}/attachment/1"])
@pytest.mark.parametrize("pk", [2 ** 53, 0, -1])
def test_a_pk_that_reads_and_names_nothing_is_no_message(app, path, pk):
    r = app.get(path.format(pk))
    assert r.json.get("detail", r.json.get("error")) in ("not found", "no such part")


@pytest.mark.parametrize("part", ["1x", "1.0", "-1", "%31", "%D9%A3", "1%0A", "x"])
def test_a_part_id_that_isnt_one_is_refused(app, world, part):
    r = app.get(f"/api/message/{world.pk(RECEIPT)}/attachment/{part}")
    assert (r.status, r.json) == (400, problems.refused("bad part id"))


def test_a_bad_pk_is_said_before_a_bad_part_id(app):
    assert app.get("/api/message/x/attachment/y").json == BAD_REQUEST


@pytest.mark.parametrize("path", ["/api/message//body", "/api/message/1/x/body", "/api/message/1/body/", "/api/message//attachment/1",
                                  "/api/message/1/attachment/", "/api/message/1/attachment/1/2", "/api/message/1/z/attachment/1"])
def test_a_path_of_any_other_shape_is_no_route(app, path):
    r = app.get(path)
    assert (r.status, r.json) == NOT_A_ROUTE


# ── a message's file ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_file_comes_as_a_download_under_a_policy_that_runs_nothing(app, world):
    pk = delivered(world, WITH_A_FILE)
    parts = {a["filename"]: a["part_id"] for a in app.get(f"/api/message/{pk}/body").json["attachments"]}
    r = app.get(f"/api/message/{pk}/attachment/{parts['../../café \"final\".pdf']}")
    assert (r.status, r.body) == (200, PDF)
    assert r.headers["content-type"] == "application/pdf"          # as the part says, lower case, its parameters gone
    assert r.headers["content-disposition"] == "attachment; filename*=UTF-8''caf%C3%A9%20_final_.pdf"
    assert r.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert r.headers["content-length"] == str(len(PDF))
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"


def test_an_active_type_keeps_its_type_and_still_downloads(app, world):
    pk = delivered(world, WITH_A_FILE)
    parts = {a["filename"]: a["part_id"] for a in app.get(f"/api/message/{pk}/body").json["attachments"]}
    r = app.get(f"/api/message/{pk}/attachment/{parts['page.html']}")
    assert r.status == 200 and b"<script>" in r.body
    assert r.headers["content-type"] == "text/html"
    assert r.headers["content-disposition"] == "attachment; filename*=UTF-8''page.html"
    assert r.headers["content-security-policy"] == "default-src 'none'; sandbox"


def test_a_file_past_what_may_go_is_the_413(app, world, monkeypatch):
    pk = delivered(world, WITH_A_FILE)
    from mailmind.http.routers import mailbox as routes
    parts = {a["filename"]: a["part_id"] for a in app.get(f"/api/message/{pk}/body").json["attachments"]}
    monkeypatch.setattr(routes, "MAX_ATTACH_BYTES", len(PDF) - 1)
    r = app.get(f"/api/message/{pk}/attachment/{parts['../../café \"final\".pdf']}")
    assert (r.status, r.json) == (413, problems.blank(413, "too large"))


def test_no_such_part_is_the_404(app, world):
    pk = delivered(world, WITH_A_FILE)
    r = app.get(f"/api/message/{pk}/attachment/99")
    assert (r.status, r.json) == (404, problems.not_found("no such part"))
    r = app.get("/api/message/999999/attachment/1")
    assert (r.status, r.json) == (404, problems.not_found("not found"))


# ── a frame's document ───────────────────────────────────────────────────────────────────────────────────────────────

def is_a_document(r):
    assert r.headers["content-type"] == "text/html; charset=utf-8"
    assert r.headers["x-frame-options"] == "SAMEORIGIN" and "sandbox" in r.headers["content-security-policy"]
    assert r.body.startswith(b"<!doctype html>")
    return True


@pytest.mark.parametrize("query", ["key=%FF", "key=%00", "key=" + "9" * 3000, "images=abc", "images=", "images=%FF", "key=a&key=b&images=1&images=0",
                                   "key=[1]", "key[]=1", "unknown=1", "key=99999999999999999999999999"])
def test_nothing_in_a_documents_query_is_refused(app, world, query):
    r = app.get(f"/api/message/{world.pk(RECEIPT)}/body.html?{query}")
    assert r.status == 404 and is_a_document(r)


def key_of(pk):
    return row_of(pk)["message_key"]


def test_a_documents_query_reads_each_names_first_value(app, world):
    pk = world.pk(RECEIPT)
    assert app.get(f"/api/message/{pk}/body.html?key={key_of(pk)}&key=zzz").status == 200
    assert app.get(f"/api/message/{pk}/body.html?key=zzz&key={key_of(pk)}").status == 404
    with_images = app.get(f"/api/message/{pk}/body.html?key={key_of(pk)}&images=1&images=0")
    assert "img-src data: https:" in with_images.headers["content-security-policy"]


def test_a_pk_with_noughts_before_it_still_names_its_row(app, world):
    pk = world.pk(RECEIPT)
    r = app.get(f"/api/message/000{pk}/body.html?key={key_of(pk)}")
    assert r.status == 200 and is_a_document(r)


def test_the_longest_pk_a_document_takes_is_a_document_and_one_more_is_no_route(app):
    r = app.get(f"/api/message/{'9' * 18}/body.html?key=1")
    assert r.status == 404 and is_a_document(r)
    r = app.get(f"/api/message/{'9' * 19}/body.html?key=1")
    assert (r.status, r.json) == NOT_A_ROUTE and r.headers["x-frame-options"] == "DENY"


def test_a_path_too_long_to_read_as_a_number_is_no_route_either(app):
    r = app.get(f"/api/message/{'9' * 5000}/body.html")
    assert (r.status, r.json) == NOT_A_ROUTE


# ── a refusal with a 200, and who counts as here ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path, said", [("/api/thread", {"ok": False, "error": "missing account/thread"}),
                                        ("/api/thread?account=&thread_id=", {"ok": False, "error": "missing account/thread"}),
                                        ("/api/message/999999/body", {"ok": False, "error": "not found"}),
                                        ("/api/message/999999/body?prefetch=1", {"ok": False, "error": "not found"})])
def test_a_get_that_finds_nothing_still_answers_a_200_that_says_so(app, path, said):
    r = app.get(path)
    assert (r.status, r.json) == (200, said)


def test_folders_with_no_account_named_say_so_with_an_empty_list(app):
    for path in ("/api/folders", "/api/folders?account="):
        r = app.get(path)
        assert (r.status, r.json) == (400, problems.refused("account required", folders=[]))


def test_undo_with_nothing_to_undo_says_so(app):
    r = app.post("/api/undo", {})
    assert (r.status, r.json) == (404, problems.not_found(empty=True))


@pytest.mark.parametrize("path, counts", [("/api/digest", True), ("/api/inboxes", True), ("/api/message/999999/body", True),
                                          ("/api/message/999999/attachment/1", True), ("/api/message/999999/body.html", True),
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
    assert app.post("/api/archive", {"pk": 1}, headers={"X-CSRF-Token": "wrong"}).status == 403
    assert app.post("/api/archive", {"pk": [1]}).status == 400
    assert app.get("/api/message/x/body").status == 400
    assert seen == []
