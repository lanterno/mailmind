"""Shared setup for the pytest-native tests in this directory.

Before any MailMind module loads, this takes any Claude key out of the environment, pins the AI
engine to the API (never a `claude` CLI call), and installs the tripwire in guard.py. Every test
then fails if the tripwire refused anything during it. The API itself is a local fake
(fakes/anthropic_api.py) for the whole session; it refuses every call that lacks its key, and
the `ai` fixture supplies that key.

`data_dir` points every module's data path at a fresh temp directory and creates the schema
there. Tests that touch the store, config, rules, views or audit trails must use it.
"""
import os
import time
from datetime import datetime, timezone

import pytest

import guard

os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["MAILMIND_AI"] = "api"
os.environ["TZ"] = "UTC"            # "today" and local times must not depend on the machine
time.tzset()
guard.install()

# The frozen "now" for golden tests: a Monday noon, a day before the corpus's Outlook invite.
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def pytest_addoption(parser):
    parser.addoption("--update-golden", action="store_true",
                     help="rewrite tests/golden/ from what the app answers now, instead of comparing")
    parser.addoption("--browser", action="store_true",
                     help="also run the tests marked browser, which drive dashboard.html in Chromium")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--browser"):
        return
    skip = pytest.mark.skip(reason="browser test: run with --browser (just test-full)")
    for item in items:
        if item.get_closest_marker("browser"):
            item.add_marker(skip)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    rep = yield
    if rep.when == "call":
        item.stash[_CALL_PASSED] = rep.passed
    return rep


_CALL_PASSED = pytest.StashKey[bool]()


@pytest.fixture(scope="session", autouse=True)
def anthropic_api():
    """The fake Claude API, there for every test and every legacy suite run from this session
    (they inherit ANTHROPIC_BASE_URL). Without the `ai` fixture there is no key, so it answers 401."""
    from fakes.anthropic_api import FakeAnthropic
    with FakeAnthropic() as fake:
        os.environ["ANTHROPIC_BASE_URL"] = fake.url
        try:
            yield fake
        finally:
            os.environ.pop("ANTHROPIC_BASE_URL", None)


@pytest.fixture(autouse=True)
def _anthropic_reset(anthropic_api):
    anthropic_api.reset()


@pytest.fixture
def ai(anthropic_api, monkeypatch):
    """AI on: a key the fake API accepts. It answers each call from the call's own schema unless
    the test queues replies (anthropic_api.queue). Returns the fake, whose .requests the test can read."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", anthropic_api.KEY)
    return anthropic_api


@pytest.fixture(autouse=True)
def _tripwire():
    guard.violations.clear()
    yield
    found = list(guard.violations)
    guard.violations.clear()
    if found:
        pytest.fail("the test tripwire refused: " + "; ".join(found), pytrace=False)


# (module, attribute, file name) for every path a module builds next to its own source.
DATA_PATHS = (
    ("store", "DB", "mailmind.db"),
    ("config", "CONFIG", "mailmind.config.json"),
    ("config", "HERE", ""),                      # config.save's temp file goes here
    ("rules", "FILE", "mailmind_rules.json"),
    ("mailclass", "RULES_FILE", "mailmind_rules.json"),
    ("views", "FILE", "mailmind_views.json"),
    ("views", "HERE", ""),                       # views.save's temp file goes here
    ("manage", "AUDIT_FILE", "sweep_audit.jsonl"),
    ("server", "RECON_AUDIT", "reconcile_audit.jsonl"),
    ("server", "SWEEP_AUDIT", "sweep_audit.jsonl"),
)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """A temp directory holding this test's database, config, rules, views and audit trails."""
    import importlib
    for mod, attr, name in DATA_PATHS:
        monkeypatch.setattr(importlib.import_module(mod), attr, str(tmp_path / name) if name else str(tmp_path))
    import mailclass
    monkeypatch.setattr(mailclass, "_rules_cache", {})
    import store
    store.init()
    return tmp_path


def _redirect(mp, directory):
    import importlib
    for mod, attr, name in DATA_PATHS:
        mp.setattr(importlib.import_module(mod), attr, os.path.join(directory, name) if name else str(directory))
    import mailclass
    mp.setattr(mailclass, "_rules_cache", {})


@pytest.fixture(scope="session")
def world_template(tmp_path_factory):
    """The world's database, built once by the app's own ingest. Tests get a copy via `world`."""
    from fakes.imap_server import FakeIMAPServer
    from fixtures import world
    import sqlite3
    import store
    d = tmp_path_factory.mktemp("world-template")
    import time_machine
    with pytest.MonkeyPatch.context() as mp, FakeIMAPServer() as srv, time_machine.travel(NOW, tick=False):
        _redirect(mp, str(d))
        placed = world.populate(srv)
        world.write_config(srv, str(d))
        store.init()
        world.ingest_all()
        db = sqlite3.connect(os.path.join(d, "mailmind.db"))
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.close()
    return os.path.join(d, "mailmind.db"), placed


@pytest.fixture
def world(world_template, data_dir):
    """A fresh copy of the world: a running fake IMAP server holding the corpus, a fake SMTP
    server the accounts send through, a config pointing at both, and the database the app built
    from the corpus, in this test's data_dir."""
    import shutil
    from fakes.imap_server import FakeIMAPServer
    from fakes.smtp_server import FakeSMTPServer
    from fixtures import world as w
    template, placed = world_template
    for suffix in ("", "-wal", "-shm"):
        p = data_dir / ("mailmind.db" + suffix)
        if p.exists():
            p.unlink()
    shutil.copyfile(template, data_dir / "mailmind.db")
    with FakeIMAPServer() as srv, FakeSMTPServer() as smtp:
        assert w.populate(srv) == placed
        w.connect_smtp(srv, smtp)
        w.write_config(srv, str(data_dir), smtp)
        yield w.World(srv, placed, data_dir, smtp)


@pytest.fixture
def app(world, monkeypatch):
    """The app's HTTP server over this test's world, with the clock frozen at NOW and every
    module's process state reset. Yields an AppClient; its .clock moves the frozen time
    (clock.shift(timedelta(...)), clock.move_to(datetime))."""
    import time_machine
    import app_client
    import outbox
    import readmutq
    import realtime
    import server
    import summarize_thread
    app_client.reset_state()
    monkeypatch.setattr(server, "CSRF", app_client.CSRF)
    # wire the workers to the server as server.__main__ does, without starting their threads
    outbox.configure(ops_lock=server._ops_lock, after_send=server._after_send)
    readmutq.configure(ops_lock=server._ops_lock, after_apply=server._after_readmut)
    summarize_thread.configure(ops_lock=server._ops_lock)
    realtime.configure(sync_handler=server.sync_guarded)
    with time_machine.travel(NOW, tick=False) as clock:
        running = app_client.RunningApp().start()
        monkeypatch.setattr(server, "PORT", running.port)
        try:
            client = app_client.AppClient(running.port)
            client.clock = clock
            yield client
            client.settle()
        finally:
            running.stop()
            app_client.reset_state()


@pytest.fixture
def golden(request, world, app):
    """Record exchanges with golden.record / golden.note; they are compared with (or, under
    --update-golden, written to) the test's file in tests/golden/ when the test passes."""
    import golden as g
    rec = g.Golden(request.node.nodeid, request.config.getoption("--update-golden"),
                   subs=[(str(world.dir), "<data>"), (f"127.0.0.1:{app.port}", "127.0.0.1:<app>"),
                         (f"localhost:{app.port}", "localhost:<app>")],
                   ports={world.server.port: "<imap>", world.smtp.port: "<smtp>", app.port: "<app>"})
    yield rec
    if request.node.stash.get(_CALL_PASSED, False):
        rec.verify()
