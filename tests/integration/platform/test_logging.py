"""mailmind.platform.logging: what reaches the terminal and the log file, and above all what doesn't.
A log is personal data, so most of these say what stays out. Every address is fictional."""
import json
import pathlib

import pytest

from fixtures import world as w
from mailmind.bootstrap import assistant, calendar, compose, curation, discovery, mailbox
from mailmind.platform import logging as logs
from mailmind.platform import profile, settings

WITHHELD = "<withheld>"
log = logs.get_logger("test")


@pytest.fixture
def home(tmp_path):
    with settings.use(settings.Settings(home=str(tmp_path))):
        logs.setup()
        yield tmp_path


def written(home, name="mailmind.log"):
    return [json.loads(line) for line in (home / name).read_text().splitlines()]


def test_each_request_through_either_door_carries_an_id_of_its_own(app, monkeypatch):
    logs.setup()
    stats = mailbox.pool.stats()
    monkeypatch.setattr(mailbox.pool, "stats", lambda: log.warning("in a GET") or stats)
    monkeypatch.setattr(mailbox.filing, "do_pending_retry", lambda id_: log.warning("in a POST") or {"ok": True})
    assert app.get("/api/debug/pool").status == 200
    assert app.get("/api/debug/pool").status == 200
    assert app.post("/api/pending/retry", {"id": 1}).status == 200
    lines = written(pathlib.Path(settings.current().home))
    assert [line["msg"] for line in lines] == ["in a GET", "in a GET", "in a POST"]
    assert len({line["request_id"] for line in lines}) == 3


# -- what the app says through it ---------------------------------------------------------------

def raises(error=None):
    def boom(*a, **k):
        raise error or RuntimeError("no such user ada@example.test, Lunch on Friday")
    return boom


def said_in_the_file():
    return written(pathlib.Path(settings.current().home))


def test_a_get_that_fails_says_its_path_and_the_class_of_its_error(app, monkeypatch, capsys):
    logs.setup()
    monkeypatch.setattr(mailbox.pool, "stats", raises())
    r = app.get("/api/debug/pool?account=ada@example.test")
    assert (r.status, r.json["detail"]) == (500, "request failed")
    out = capsys.readouterr().out
    assert "[http] GET /api/debug/pool failed: RuntimeError\n" in out
    assert "example.test" not in out and "Lunch" not in out
    [line] = said_in_the_file()
    assert (line["path"], line["error"], line["level"]) == ("/api/debug/pool", "RuntimeError", "ERROR")
    assert line["request_id"] and "example.test" not in json.dumps(line)


def test_a_post_that_fails_says_its_path_and_not_its_query(app, monkeypatch, capsys):
    logs.setup()
    monkeypatch.setattr(mailbox.filing, "do_pending_retry", raises())
    assert app.post("/api/pending/retry?folder=Lunch+on+Friday", {"id": 1}).status == 500
    out = capsys.readouterr().out
    assert "[http] POST /api/pending/retry failed: RuntimeError\n" in out
    assert "Lunch" not in out
    assert "Lunch" not in json.dumps(said_in_the_file())


def test_a_write_refused_read_only_says_what_was_refused(app, monkeypatch, capsys):
    monkeypatch.setattr(mailbox.filing, "do_pending_retry", raises(profile.ReadOnlyError("a sweep")))
    assert app.post("/api/pending/retry", {"id": 1}).status == 409
    assert "[http] POST /api/pending/retry refused, read-only: a sweep\n" in capsys.readouterr().out


@pytest.mark.parametrize("path, holder, name, said", [
    ("/api/invite/rsvp", lambda: calendar.rsvp, "respond", "[rsvp] error: RuntimeError"),
    ("/api/cleanup/execute", lambda: curation.cleanup, "execute", "[cleanup] error: RuntimeError"),
    ("/api/contacts/known", lambda: discovery.known, "ask", "[contacts] known error: RuntimeError"),
    ("/api/ai/ask", lambda: assistant.asking, "ask", "[ai] ask error: RuntimeError"),
    ("/api/send", lambda: compose.sending, "send", "[send] error: RuntimeError"),
    ("/api/draft/resume", lambda: compose.drafts, "resume", "[resume] error: RuntimeError"),
])
def test_a_route_that_answers_its_own_failure_says_only_its_class(app, world, monkeypatch, capsys, path, holder, name, said):
    monkeypatch.setattr(holder(), name, raises())
    asked = {"key": world.key("ada-inbox-06-outlook-invite.eml")} if path == "/api/invite/rsvp" else {}    # an RSVP names its invite
    assert app.post(path, asked).status == 500
    out = capsys.readouterr().out
    assert said + "\n" in out
    assert "example.test" not in out and "Lunch" not in out


def test_a_sync_that_fails_withholds_its_account(world, monkeypatch, capsys):
    monkeypatch.setattr(mailbox.ingest, "sync_new", raises())
    capsys.readouterr()
    assert mailbox.syncing.sync_accounts({w.ADA}) == 0
    assert capsys.readouterr().out == f"[sync] {WITHHELD}: sync failed: RuntimeError\n"
