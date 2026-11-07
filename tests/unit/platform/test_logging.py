"""mailmind.platform.logging: what reaches the terminal and the log file, and above all what doesn't.
A log is personal data, so most of these say what stays out. Every address is fictional."""
import ast
import dataclasses
import fnmatch
import json
import logging as stdlib_logging
import os
import pathlib
import stat
import sys
import threading

import pytest

import guard
from fixtures import world as w
from mailmind.bootstrap import assistant, background, calendar, compose, curation, discovery, mailbox, wiring
from mailmind.bootstrap.mailbox import realtime
from mailmind.http import serving
from mailmind.platform import logging as logs
from mailmind.platform import profile, settings
from mailmind.platform.bus import Bus
from mailmind.platform.workers.scheduler import Scheduler

WITHHELD = "<withheld>"
log = logs.get_logger("test")


@pytest.fixture
def home(tmp_path):
    with settings.use(settings.Settings(home=str(tmp_path))):
        logs.setup()
        yield tmp_path


def written(home, name="mailmind.log"):
    return [json.loads(line) for line in (home / name).read_text().splitlines()]


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


# -- what is withheld -----------------------------------------------------------------

@pytest.mark.parametrize("value", ["ada@example.test", "Ada Lin <ada@example.test>", "ADA@EXAMPLE.TEST",
                                   "mailto:ada@example.test", "/api/x?account=ada%40example.test",
                                   "ada@bücher.example.test", "@example.test"])
def test_anything_that_looks_like_an_address_is_withheld_even_under_an_allowed_name(home, capsys, value):
    log.warning("about %(what)s", {"what": value})
    out = capsys.readouterr().out
    [line] = written(home)
    assert WITHHELD in out and WITHHELD in line["msg"] and WITHHELD in line["what"]
    assert "example.test" not in out.lower() and "example.test" not in json.dumps(line).lower()


def test_an_address_in_the_message_itself_is_withheld(home, capsys):
    log.warning("written to ada@example.test by mistake")
    assert written(home)[0]["msg"] == f"written to {WITHHELD} by mistake"
    assert capsys.readouterr().out == f"[test] written to {WITHHELD} by mistake\n"


@pytest.mark.parametrize("name", ["subject", "body", "prompt", "secret", "address", "folder", "whatever"])
def test_a_name_not_on_the_allow_list_is_withheld(home, capsys, name):
    log.warning(f"sent %({name})s", {name: "Lunch on Friday"})
    out = capsys.readouterr().out
    [line] = written(home)
    assert (line["msg"], line[name]) == (f"sent {WITHHELD}", WITHHELD)
    assert out == f"[test] sent {WITHHELD}\n"
    assert "Lunch" not in json.dumps(line)


def test_an_extra_is_held_to_the_same_list(home, capsys):
    log.warning("sent", extra={"subject": "Lunch on Friday", "job": "sync"})
    [line] = written(home)
    assert (line["subject"], line["job"]) == (WITHHELD, "sync")
    assert "Lunch" not in capsys.readouterr().out


def test_a_positional_argument_shows_only_if_it_is_a_number(home):
    log.warning("%s sent %d, %s", "Lunch on Friday", 3, "ada@example.test")
    assert written(home)[0]["msg"] == f"{WITHHELD} sent 3, {WITHHELD}"


def test_a_count_shows(home, capsys):
    log.info("moved %(count)s", {"count": 3})
    assert capsys.readouterr().out == "[test] moved 3\n"


def test_a_value_that_isnt_plain_is_withheld(home):
    log.warning("%(what)s", {"what": {"subject": "Lunch on Friday"}})
    [line] = written(home)
    assert (line["msg"], line["what"]) == (WITHHELD, WITHHELD)


def test_a_message_that_isnt_a_string_is_withheld(home):
    log.warning(ValueError("Lunch on Friday"))
    assert written(home)[0]["msg"] == WITHHELD


def test_a_message_that_wont_take_its_arguments_says_what_it_can():
    """Asked of the filter alone, since pytest's own capture of a record raises on one of these."""
    record = stdlib_logging.LogRecord("mailmind.test", stdlib_logging.WARNING, "", 0, "%(nothing)s and %(what)d",
                                      ({"what": "Lunch"},), None)
    assert logs.Redact().filter(record).getMessage() == "%(nothing)s and %(what)d"


def test_of_an_exception_only_its_class_name_shows(home, capsys):
    boom = RuntimeError("no such user ada@example.test, Lunch on Friday")
    log.warning("%(job)s failed", {"job": "sync"}, exc_info=boom)
    try:
        raise boom
    except RuntimeError:
        log.exception("again")
    out = capsys.readouterr().out
    assert out == "[test] sync failed: RuntimeError\n[test] again: RuntimeError\n"
    first, second = written(home)
    assert (first["msg"], first["error"], first["job"]) == ("sync failed", "RuntimeError", "sync")
    assert second["error"] == "RuntimeError"
    assert "Traceback" not in json.dumps([first, second]) and "Lunch" not in json.dumps([first, second])


def test_only_the_exception_says_what_the_error_was(home, capsys):
    log.warning("failed", {"error": "Lunch on Friday"})
    assert written(home)[0]["error"] == WITHHELD
    assert "Lunch" not in capsys.readouterr().out


def test_a_value_cant_forge_a_line(home, capsys):
    log.warning("%(path)s failed", {"path": "/api/x\n[sync] forged\r\n{\"level\": \"forged\"}"})
    assert len(capsys.readouterr().out.splitlines()) == 1
    assert len(written(home)) == 1


def test_a_long_value_is_cut(home):
    log.warning("%(path)s", {"path": "/" + "x" * 50_000})
    [line] = written(home)
    assert len(line["msg"]) < 1000 and len(line["path"]) < 1000


def test_no_handler_but_its_own_hears_a_record():
    assert stdlib_logging.getLogger("mailmind").propagate is False


def test_a_debug_line_says_nothing_unless_asked(home, capsys):
    log.debug("%(what)s", {"what": "ada@example.test"})
    assert capsys.readouterr().out == ""
    assert not (home / "mailmind.log").exists()


# -- asking for the detail --------------------------------------------------------------

def test_debug_shows_the_detail_at_the_terminal_and_never_in_the_file(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("MAILMIND_LOG_LEVEL", "debug")
    with settings.use(settings.Settings(home=str(tmp_path))):
        logs.setup()
    log.debug("%(what)s", {"what": "ada@example.test"})
    log.warning("sent %(subject)s", {"subject": "Lunch on Friday"}, exc_info=RuntimeError("no bo@example.test"))
    out = capsys.readouterr().out
    assert "ada@example.test" in out and "Lunch on Friday" in out and "no bo@example.test" in out
    [line] = written(tmp_path)
    assert line["level"] == "WARNING" and "example.test" not in json.dumps(line) and "Lunch" not in json.dumps(line)
    logs.stop()
    log.warning("sent %(subject)s", {"subject": "Lunch on Friday"})
    assert "Lunch" not in capsys.readouterr().out


# -- the two places a line goes -----------------------------------------------------------

def test_the_terminal_gets_a_line_a_person_reads(home, capsys):
    logs.get_logger("jobs").warning("%(job)s failed", {"job": "sync"}, exc_info=ConnectionResetError("no"))
    logs.get_logger().info("MailMind on port %(port)s", {"port": 8721})
    assert capsys.readouterr().out == "[jobs] sync failed: ConnectionResetError\nMailMind on port 8721\n"


def test_the_file_gets_a_json_object_a_line(home):
    logs.get_logger("jobs").warning("%(job)s failed", {"job": "sync"}, exc_info=ConnectionResetError("no"))
    logs.get_logger().info("up")
    first, second = written(home)
    assert first.pop("ts").endswith("+00:00")
    assert first == {"level": "WARNING", "logger": "jobs", "msg": "sync failed", "error": "ConnectionResetError",
                     "job": "sync"}
    assert (second["level"], second["logger"], second["msg"]) == ("INFO", "", "up")


def test_before_setup_the_terminal_hears_it_and_nothing_is_written(tmp_path, capsys):
    with settings.use(settings.Settings(home=str(tmp_path))):
        log.warning("before")
    assert capsys.readouterr().out == "[test] before\n"
    assert list(tmp_path.iterdir()) == []


def test_setup_again_adds_nothing(home, capsys):
    logs.setup()
    log.warning("once")
    assert capsys.readouterr().out == "[test] once\n"
    assert len(written(home)) == 1


def test_setup_under_other_settings_moves_the_file(home, tmp_path_factory):
    other = tmp_path_factory.mktemp("other")
    log.warning("here")
    with settings.use(settings.Settings(home=str(other))):
        logs.setup()
        log.warning("there")
    assert [line["msg"] for line in written(home)] == ["here"]
    assert [line["msg"] for line in written(other)] == ["there"]


def test_the_file_is_its_owners_alone_whatever_the_umask(home):
    was = os.umask(0)
    try:
        log.warning("one")
    finally:
        os.umask(was)
    assert mode(home / "mailmind.log") == 0o600


def test_a_file_already_there_is_brought_into_line(tmp_path):
    p = tmp_path / "mailmind.log"
    p.write_text("")
    p.chmod(0o644)
    with settings.use(settings.Settings(home=str(tmp_path))):
        logs.setup()
        log.warning("one")
    assert mode(p) == 0o600


def test_the_file_rotates_keeps_its_backups_and_each_is_its_owners_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "MAX_BYTES", 400)
    monkeypatch.setattr(logs, "BACKUPS", 2)
    with settings.use(settings.Settings(home=str(tmp_path))):
        logs.setup()
        for n in range(100):
            log.warning("line %d", n)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["mailmind.log", "mailmind.log.1", "mailmind.log.2"]
    assert {mode(p) for p in tmp_path.iterdir()} == {0o600}
    assert all(p.stat().st_size <= 400 for p in tmp_path.iterdir())
    assert written(tmp_path)[-1]["msg"] == "line 99"


def test_the_log_files_are_gitignored():
    patterns = [line.strip() for line in (pathlib.Path(guard.ROOT) / ".gitignore").read_text().splitlines()]
    for name in ("mailmind.log", "mailmind.log.1"):
        assert any(fnmatch.fnmatchcase(name, p) for p in patterns), name


def test_a_file_that_cant_be_written_stops_nothing(tmp_path, capsys):
    with settings.use(settings.Settings(home=str(tmp_path / "gone"))):
        logs.setup()
        log.warning("still said")
    got = capsys.readouterr()
    assert got.out == "[test] still said\n" and got.err == ""


def test_uvicorn_setting_up_its_own_logging_leaves_ours_going(home, capsys):
    """Building its Config closes every handler in the process, ours included."""
    serving.config(lambda: None, port=1)
    log.warning("after")
    assert capsys.readouterr().out == "[test] after\n"
    assert [line["msg"] for line in written(home)] == ["after"]


def test_each_start_of_the_app_opens_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(realtime, "start", lambda: None)
    monkeypatch.setattr(background, "JOBS", background.JOBS)
    with settings.use(settings.Settings(home=str(tmp_path))):
        wiring.start(spawn=lambda target: None)
        wiring.start(spawn=lambda target: None)
        wiring.unwire()
        capsys.readouterr()
        log.warning("started")
    assert capsys.readouterr().out == "[test] started\n"
    assert [line["msg"] for line in written(tmp_path) if line["logger"] == "test"] == ["started"]


# -- correlation ids ----------------------------------------------------------------------

def test_a_line_outside_any_request_or_round_carries_no_id(home):
    log.warning("alone")
    assert not {"request_id", "job_id"} & set(written(home)[0])


def test_a_line_carries_the_id_of_the_request_and_the_round_it_happened_in(home):
    with logs.for_request() as request:
        log.warning("in the request")
        with logs.for_round("sync") as job:
            log.warning("in both")
        log.warning("back in the request")
    log.warning("out")
    first, both, back, out = written(home)
    assert first["request_id"] == request and "job_id" not in first
    assert (both["request_id"], both["job_id"]) == (request, job)
    assert job.startswith("sync-")
    assert back["request_id"] == request and "job_id" not in back
    assert "request_id" not in out


def test_each_request_gets_an_id_of_its_own_that_stays_on_its_thread(home):
    seen = []

    def elsewhere():
        seen.append(logs.correlation())
    with logs.for_request() as one:
        t = threading.Thread(target=elsewhere)
        t.start()
        t.join()
    with logs.for_request() as two:
        pass
    assert one != two
    assert seen == [{}]


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


def test_each_round_of_a_job_carries_an_id_of_its_own(home):
    jobs = Scheduler()
    jobs.add("sync", lambda: log.warning("in the round"), every=300)
    jobs.run("sync")
    jobs.run("sync")
    log.warning("after the rounds")
    first, second, after = written(home)
    assert first["job_id"].startswith("sync-") and second["job_id"].startswith("sync-")
    assert first["job_id"] != second["job_id"]
    assert "job_id" not in after


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
    assert (r.status, r.json["error"]) == (500, "request failed")
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
def test_a_route_that_answers_its_own_failure_says_only_its_class(app, monkeypatch, capsys, path, holder, name, said):
    monkeypatch.setattr(holder(), name, raises())
    assert app.post(path, {}).status == 500
    out = capsys.readouterr().out
    assert said + "\n" in out
    assert "example.test" not in out and "Lunch" not in out


def test_a_snapshot_that_fails_says_only_its_class(monkeypatch, capsys):
    from mailmind.http.routers import export
    monkeypatch.setattr(export.export, "export_db", raises())
    export._snapshot(None)                               # it fails before it has answered anything
    assert capsys.readouterr().out == "[export] db error: RuntimeError\n"


def test_a_round_that_fails_says_which_job_and_the_class_of_its_error(home, capsys):
    jobs = Scheduler()
    jobs.add("sync", raises(), every=300)
    jobs.run("sync")
    assert capsys.readouterr().out == "[jobs] sync failed: RuntimeError\n"
    [line] = written(home)
    assert (line["job"], line["error"]) == ("sync", "RuntimeError")
    assert line["job_id"].startswith("sync-") and "example.test" not in json.dumps(line)


def test_a_sync_that_fails_withholds_its_account(world, monkeypatch, capsys):
    monkeypatch.setattr(mailbox.ingest, "sync_new", raises())
    capsys.readouterr()
    assert mailbox.syncing.sync_accounts({w.ADA}) == 0
    assert capsys.readouterr().out == f"[sync] {WITHHELD}: sync failed: RuntimeError\n"


def test_expiry_withholds_its_account_and_the_text_of_an_error(monkeypatch, capsys):
    outcome = {"error": "RuntimeError: no such user ada@example.test, Lunch on Friday", "moved": 0}
    monkeypatch.setattr(curation.expiry, "account", lambda acct: outcome)
    curation.expiry.auto_one({"email": "ada@example.test"})
    assert capsys.readouterr().out == f"[expire] {WITHHELD}: RuntimeError\n"


def test_a_bus_handler_that_fails_goes_in_the_file_too(home):
    @dataclasses.dataclass(frozen=True)
    class Happened:
        pass
    bus = Bus()
    bus.subscribe(Happened, raises())
    bus.publish(Happened())
    [line] = written(home)
    assert (line["event"], line["error"]) == ("Happened", "RuntimeError")
    assert "example.test" not in json.dumps(line)


def test_each_start_says_its_startup_lines_in_the_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(realtime, "start", lambda: None)
    monkeypatch.setattr(background, "JOBS", background.JOBS)
    with settings.use(settings.Settings(home=str(tmp_path))):
        wiring.start(spawn=lambda target: None)
        wiring.unwire()
    out = capsys.readouterr().out
    assert "[imap] mail through imaplib\n" in out
    by_logger = {line["logger"]: line for line in written(tmp_path)}
    assert by_logger["imap"]["msg"] == "mail through imaplib"
    assert by_logger["ai"]["msg"] in out and "Claude API" in by_logger["ai"]["msg"]


# -- how the package uses it -----------------------------------------------------------------

def test_the_name_logging_stays_the_standard_librarys():
    assert sys.modules["logging"] is stdlib_logging
    assert stdlib_logging.__name__ == "logging" and logs.__name__ == "mailmind.platform.logging"
    assert os.path.dirname(stdlib_logging.__file__) != os.path.dirname(logs.__file__)


def _source_files():
    return (pathlib.Path(guard.ROOT) / "mailmind").rglob("*.py")


def test_no_module_binds_the_name_logging_to_this_one():
    for path in _source_files():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module == "mailmind.platform":
                assert not any(a.name == "logging" and a.asname in (None, "logging") for a in node.names), path


def _where_it_prints(tree):
    """The name of each function a print is called in, and None for one outside any."""
    def walk(node, where):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call) and getattr(child.func, "id", "") == "print":
                yield where
            yield from walk(child, child.name if isinstance(child, ast.FunctionDef) else where)
    return set(walk(tree, None))


def test_only_a_commands_own_output_is_a_print():
    """A print goes past `Redact`, so a line about the app's own running goes through the log."""
    package = pathlib.Path(guard.ROOT) / "mailmind"
    printing = {path.relative_to(package).as_posix(): where for path in _source_files()
                if (where := _where_it_prints(ast.parse(path.read_text())))}
    for the_command_line in ("__main__.py", "bootstrap/cli.py"):
        del printing[the_command_line]
    assert printing == {"accounts/application/directory.py": {"check", "materialize"},
                        "bootstrap/wiring.py": {"assess_batch"}}


def _is_a_logger(node):
    if isinstance(node, ast.Call):
        return getattr(node.func, "id", "") == "get_logger"
    name = getattr(node, "id", None) or getattr(node, "attr", "")
    return name.endswith(("log", "logger"))


def test_every_message_is_a_literal():
    """An f-string would put a value past the allow list, and a filter can't tell afterwards."""
    levels = {"debug", "info", "warning", "error", "exception", "critical"}
    for path in _source_files():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in levels and _is_a_logger(node.func.value):
                first = node.args[0]
                assert isinstance(first, ast.Constant) and isinstance(first.value, str), f"{path}:{node.lineno}"
