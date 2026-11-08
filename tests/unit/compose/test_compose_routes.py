"""Compose's routes on their router: what the schemas refuse, what they let by, how far a body may run, and each route's own catch."""
import base64
import http.client
import json
from datetime import datetime, timezone

import pytest

import app_client
import problems
from fixtures import world as w
from mailmind.bootstrap import assistant, compose
from mailmind.compose.api import MAX_SEND_POST
from mailmind.compose.domain.limits import MAX_ATTACH_BYTES
from mailmind.http.shared import MAX_POST
from mailmind.kernel.errors import Locked, NotFound
from mailmind.platform import profile

BAD_REQUEST = problems.BAD_REQUEST
AT_SIX = int(datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc).timestamp())    # later today, by the frozen clock
SECRET = "hunter2-secret-token"
WITH_A_PDF = "bea-drafts-01-apple-mail.eml"


def new(**more):
    return {"account": w.ADA, "mode": "new", "to": ["lin@example.test"], "subject": "Hi", "text": "Hello", **more}


def forward(world, **more):
    return {"account": w.BEA, "mode": "forward", "in_reply_to_pk": world.pk(WITH_A_PDF), "to": ["ivo@harbor.example.test"], "text": "As promised.",
            "autosave": True, **more}


def raw(app, path, body, headers=None, chunked=False):
    """A POST whose body and length the test says itself. `http.client` says no length only of a chunked body."""
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=60)
    try:
        conn.request("POST", path, body=body, encode_chunked=chunked,
                     headers={"Host": f"127.0.0.1:{app.port}", "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": app_client.CSRF, "Origin": app.origin,
                              "Content-Type": "application/json", **(headers or {})})
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


# ── what the schemas refuse ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/api/send", "/api/draft"])
def test_a_value_its_model_refuses_is_the_400(app, path):
    for field, junk in (("to", 5), ("to", [1]), ("to", {"a": 1}), ("to", [None]), ("cc", 5), ("bcc", [["x"]]),
                        ("attachments", "x"), ("attachments", 5), ("attachments", {"filename": "x"}),
                        ("resume", "x"), ("resume", [1]), ("resume", 5),
                        ("in_reply_to_pk", "x"), ("in_reply_to_pk", [1]), ("in_reply_to_pk", 10 ** 30),
                        ("forward_attachments", "no"), ("forward_attachments", 0),
                        ("draft_id", [1]), ("idempotency_key", 5)):
        r = app.post(path, new(**{field: junk}))
        assert (r.status, r.json) == (400, BAD_REQUEST), (field, junk)
    answer = app.get("/api/outbox").json
    assert not answer["items"] and not answer["drafts"]


def test_a_send_time_that_cant_be_one(app):
    for junk in ("soon", [1], {"at": 1}, float("inf"), float("nan"), 10 ** 30, ""):
        r = app.post("/api/send", new(send_after=junk))
        assert (r.status, r.json) == (400, BAD_REQUEST), junk
    assert not app.get("/api/outbox").json["items"]


def test_what_only_a_save_reads_is_refused_there(app):
    for field, junk in (("delete", "yes"), ("delete", 1), ("autosave", 1), ("autosave", "no"), ("base_version", "x"), ("base_version", [1])):
        r = app.post("/api/draft", new(**{field: junk}))
        assert (r.status, r.json) == (400, BAD_REQUEST), (field, junk)


@pytest.mark.parametrize("path", ["/api/outbox/retry", "/api/outbox/cancel", "/api/outbox/sendnow"])
def test_a_queued_send_must_be_named(app, path):
    for body in ({}, {"id": None}, {"id": [1]}):
        r = app.post(path, body)
        assert (r.status, r.json) == (400, BAD_REQUEST), body


@pytest.mark.parametrize("path", ["/api/send", "/api/draft", "/api/draft/resume", "/api/outbox/cancel"])
def test_a_body_that_isnt_a_json_object(app, path):
    for body in (b"not json", b"[1, 2]", b'"text"', b"5", b"null"):
        status, said = raw(app, path, body)
        assert (status, json.loads(said)) == (400, BAD_REQUEST), body


# ── what is its use case's to judge ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/api/send", "/api/draft"])
def test_a_file_is_judged_in_its_own_words(app, path):
    for files, said in (([5], "bad attachment"), (["x"], "bad attachment"), ([{"filename": 5, "b64": "aGk="}], "bad attachment"),
                        ([{"filename": "x.bin", "b64": "%%%"}], "bad attachment encoding")):
        r = app.post(path, new(attachments=files))
        assert (r.status, r.json) == (400, problems.refused(said)), files


def test_what_a_resume_holds_is_read_as_text(app):
    """So a dict is all the model asks of it."""
    saved = app.post("/api/draft", new(autosave=True, resume={"tail_text": 5, "in_reply_to": 5, "references": [1], "nobody": "reads this"})).json
    assert saved["ok"] is True
    opened = app.post("/api/draft/resume", {"draft_id": saved["draft_id"]}).json
    assert opened["text"] + opened["tail_text"] == "Hello5"


def test_a_recipient_may_be_a_string_of_them(app):
    r = app.post("/api/send", new(to="lin@example.test, Bo <bo@example.test>", cc="", bcc=None))
    assert (r.status, r.json["recipients"]) == (200, 2)


@pytest.mark.parametrize("to, said", [(["lin at example dot test"], "not a valid address: 'lin'"), ([], "at least one recipient is required")])
def test_a_recipient_is_judged_in_its_own_words(app, to, said):
    r = app.post("/api/send", new(to=to))
    assert (r.status, r.json) == (400, problems.refused(said))


def test_a_send_time_that_reads_as_one(app):
    for send_after, scheduled in ((0, False), (None, False), (False, False), (AT_SIX, True), (str(AT_SIX), True), (float(AT_SIX), True)):
        r = app.post("/api/send", new(send_after=send_after))
        assert (r.status, r.json["ok"], r.json["scheduled"]) == (200, True, scheduled), send_after


def test_a_time_that_has_passed_is_still_the_use_cases(app):
    r = app.post("/api/send", new(send_after=AT_SIX - 86400))
    assert (r.status, r.json) == (400, problems.refused("that send time has passed"))


# ── what they let by ─────────────────────────────────────────────────────────────────────────────

def test_a_field_nothing_here_reads_is_nothing_to_refuse(app):
    for field in ("thread_id", "from_addr", "token", "decision", "undo_group", "email", "padding"):
        sent = app.post("/api/send", new(**{field: 5}))
        assert (sent.status, sent.json["ok"]) == (200, True), field
        saved = app.post("/api/draft", new(autosave=True, **{field: [1]}))
        assert (saved.status, saved.json["ok"]) == (200, True), field
        assert app.post("/api/draft/resume", {"draft_id": saved.json["draft_id"], field: 5}).json["ok"] is True
        cancelled = app.post("/api/outbox/cancel", {"id": sent.json["outbox_id"], "subject": 5, field: 5})
        assert (cancelled.status, cancelled.json) == (200, {"ok": True}), field


def test_a_forward_left_to_itself_takes_the_files_along(app, world):
    """The use case tells `forward_attachments` left out from one that says no, so what was left out has to stay out."""
    taken = app.post("/api/draft", forward(world)).json
    assert [f["content_type"] for f in taken["files"]] == ["application/pdf"]
    assert app.post("/api/draft", forward(world, forward_attachments=False)).json["files"] == []
    assert app.post("/api/draft", forward(world, forward_attachments=None)).json["files"] == []


def test_a_mode_left_out_is_a_new_message(app):
    saved = app.post("/api/draft", {"account": w.ADA, "to": ["lin@example.test"], "text": "Hello", "autosave": True}).json
    assert app.post("/api/draft/resume", {"draft_id": saved["draft_id"]}).json["mode"] == "new"


def test_a_version_left_out_overwrites_and_any_other_is_held_to_it(app):
    first = app.post("/api/draft", new(autosave=True)).json
    assert first["version"] == 1
    for stale in (0, 7):
        r = app.post("/api/draft", new(autosave=True, draft_id=first["draft_id"], base_version=stale))
        assert (r.status, r.json) == (409, problems.conflict("This draft changed in another window.", conflict=True, version=1))
    assert app.post("/api/draft", new(autosave=True, draft_id=first["draft_id"], base_version=None)).json["version"] == 2
    assert app.post("/api/draft", new(autosave=True, draft_id=first["draft_id"])).json["version"] == 3


def test_a_number_may_still_come_as_text(app):
    saved = app.post("/api/draft", new(autosave=True)).json
    did = str(saved["draft_id"])
    assert app.post("/api/draft", new(autosave=True, draft_id=did, base_version="1")).json["version"] == 2
    assert app.post("/api/draft/resume", {"draft_id": did}).json["draft_id"] == saved["draft_id"]
    sent = app.post("/api/send", new()).json
    assert app.post("/api/outbox/cancel", {"id": str(sent["outbox_id"])}).json == {"ok": True}


def test_a_save_with_nothing_in_it_says_which_draft_it_named(app):
    empty = {"account": w.ADA, "mode": "new"}
    assert app.post("/api/draft", empty).json == {"ok": True, "draft_id": None, "empty": True}
    assert app.post("/api/draft", {**empty, "draft_id": 7}).json == {"ok": True, "draft_id": 7, "empty": True}


def test_a_discard_names_its_draft(app):
    saved = app.post("/api/draft", new(autosave=True)).json
    assert app.post("/api/draft", {"delete": True, "draft_id": saved["draft_id"]}).json == {"ok": True, "deleted": True}


def test_what_names_no_queued_send(app):
    for path in ("/api/outbox/retry", "/api/outbox/sendnow"):
        r = app.post(path, {"id": 999999})
        assert (r.status, r.json) == (404, problems.not_found())
    r = app.post("/api/outbox/cancel", {"id": 999999})
    assert (r.status, r.json) == (200, {"ok": False})


def test_how_much_of_the_outbox_it_lists(app):
    first, second = (app.post("/api/send", new(idempotency_key=key)).json["outbox_id"] for key in ("k-1", "k-2"))
    assert len(app.get("/api/outbox").json["items"]) == 2
    assert len(app.get("/api/outbox?limit=").json["items"]) == 2
    assert len(app.get("/api/outbox?limit=1").json["items"]) == 1
    assert len(app.get("/api/outbox?limit=1&limit=2").json["items"]) == 1
    assert {first, second} == {item["id"] for item in app.get("/api/outbox").json["items"]}


# ── how far a body may run ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path, extra", [("/api/send", {}), ("/api/draft", {"autosave": True})])
def test_a_message_with_all_the_files_it_may_carry_goes(app, path, extra):
    """Well past what any other route may carry."""
    b64 = base64.b64encode(b"x" * MAX_ATTACH_BYTES).decode()
    assert MAX_POST < len(b64) < MAX_SEND_POST
    r = app.post(path, new(attachments=[{"filename": "big.bin", "content_type": "application/octet-stream", "b64": b64}], **extra))
    assert (r.status, r.json["ok"]) == (200, True)


@pytest.mark.parametrize("path", ["/api/send", "/api/draft"])
def test_more_files_than_it_may_carry_is_the_use_cases_refusal(app, path):
    b64 = base64.b64encode(b"x" * (MAX_ATTACH_BYTES + 1)).decode()
    r = app.post(path, new(attachments=[{"filename": "big.bin", "b64": b64}]))
    assert (r.status, r.json) == (400, problems.refused("attachments too large"))


@pytest.mark.parametrize("path, extra", [("/api/send", {}), ("/api/draft", {"autosave": True})])
def test_a_body_as_long_as_its_cap_is_read(app, path, extra):
    short = json.dumps(new(padding="", **extra)).encode()
    body = json.dumps(new(padding="x" * (MAX_SEND_POST - len(short)), **extra)).encode()
    assert len(body) == MAX_SEND_POST
    status, said = raw(app, path, body)
    assert (status, json.loads(said)["ok"]) == (200, True)


def test_a_body_that_says_it_runs_past_its_cap_is_the_413_unread(app):
    for path, cap in (("/api/send", MAX_SEND_POST), ("/api/draft", MAX_SEND_POST), ("/api/draft/resume", MAX_POST), ("/api/outbox/retry", MAX_POST),
                      ("/api/outbox/cancel", MAX_POST), ("/api/outbox/sendnow", MAX_POST)):
        status, said = raw(app, path, b"{}", {"Content-Length": str(cap + 1)})
        assert (status, json.loads(said)) == (413, problems.blank(413, "payload too large")), path
    assert not app.get("/api/outbox").json["items"]


@pytest.mark.parametrize("path, cap", [("/api/send", MAX_SEND_POST), ("/api/draft/resume", MAX_POST)])
def test_a_body_that_never_says_runs_past_its_cap_and_is_the_413(app, path, cap):
    status, said = raw(app, path, iter([b"x" * cap, b"y"]), {"Transfer-Encoding": "chunked"}, chunked=True)
    assert (status, json.loads(said)) == (413, problems.blank(413, "payload too large"))


def test_only_a_send_and_a_save_get_the_larger_cap(app):
    """Reopening a draft only names it."""
    status, said = raw(app, "/api/draft/resume", json.dumps({"pk": 1, "padding": "x" * MAX_POST}).encode())
    assert (status, json.loads(said)) == (413, problems.blank(413, "payload too large"))


# ── each route's own catch ───────────────────────────────────────────────────────────────────────

def _raises(error):
    def boom(*args, **kwargs):
        raise error
    return boom


def test_what_a_send_raises(app, monkeypatch):
    for error, status, answer in ((RuntimeError(SECRET), 500, problems.blank(500, "send failed")),
                                  (KeyError(SECRET), 500, problems.blank(500, "send failed")),
                                  (profile.ReadOnlyError(SECRET), 500, problems.blank(500, "send failed")),
                                  (ValueError("our own words"), 400, problems.refused("our own words")),
                                  (NotFound("not there"), 404, problems.not_found("not there"))):
        monkeypatch.setattr(compose.sending, "send", _raises(error))
        r = app.post("/api/send", new())
        assert (r.status, r.json) == (status, answer), error


def test_what_a_save_raises(app, monkeypatch):
    for error, status, answer in ((RuntimeError(SECRET), 500, problems.FAILED),
                                  (profile.ReadOnlyError(SECRET), 409, problems.read_only()),
                                  (ValueError("our own words"), 400, problems.refused("our own words")),
                                  (Locked("held"), 423, problems.locked("held"))):
        monkeypatch.setattr(compose.drafts, "save", _raises(error))
        r = app.post("/api/draft", new())
        assert (r.status, r.json) == (status, answer), error


def test_what_a_reopening_raises(app, monkeypatch):
    for error, status, answer in ((RuntimeError(SECRET), 500, problems.blank(500, "Couldn’t open this draft.")),
                                  (ValueError(SECRET), 500, problems.blank(500, "Couldn’t open this draft.")),
                                  (Locked("held"), 423, problems.locked("held"))):
        monkeypatch.setattr(compose.drafts, "resume", _raises(error))
        r = app.post("/api/draft/resume", {"pk": 1})
        assert (r.status, r.json) == (status, answer), error


def test_a_request_counts_as_someone_here_unless_the_tray_polled(app, monkeypatch):
    seen = []
    monkeypatch.setattr(assistant.governor(), "saw_someone", lambda: seen.append(1))
    assert app.get("/api/outbox").status == 200
    assert app.post("/api/draft", {"account": w.ADA, "to": [1]}).status == 400
    assert seen == []
    assert app.post("/api/draft", new(autosave=True)).status == 200
    assert seen == [1]
