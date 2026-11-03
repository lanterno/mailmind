"""Shared setup for the pytest-native tests in this directory.

Before any MailMind module loads, this takes any Claude key out of the environment, pins the AI
engine to the API (never a `claude` CLI call), and installs the tripwire in guard.py. It takes
MAILMIND_HOME out too, so a test that forgets data_dir lands on the repo root, and has the
tripwire guard the directory it named as it guards the repo root. Every test
then fails if the tripwire refused anything during it. The API itself is a local fake
(fakes/anthropic_api.py) for the whole session; it refuses every call that lacks its key, and
the `ai` fixture supplies that key.

`data_dir` makes a fresh temp directory the app's data directory (mailmind.platform.settings)
and creates the schema there. Tests that touch the store, config, rules, views or audit trails
must use it.
"""
import os
import time
from datetime import datetime, timezone

import pytest

import guard

os.environ.pop("ANTHROPIC_API_KEY", None)
_home = os.environ.pop("MAILMIND_HOME", "")
os.environ["MAILMIND_AI"] = "api"
os.environ["TZ"] = "UTC"            # "today" and local times must not depend on the machine
time.tzset()
if _home:
    guard.protect(_home)
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


@pytest.fixture
def data_dir(tmp_path):
    """A temp directory holding this test's database, config, rules, views and audit trails."""
    from mailmind.platform import settings
    import store
    with settings.use(settings.Settings(home=str(tmp_path))):
        store.init()
        yield tmp_path


@pytest.fixture(scope="session")
def world_template(tmp_path_factory):
    """The world's database, built once by the app's own ingest. Tests get a copy via `world`."""
    from fakes.imap_server import FakeIMAPServer
    from fixtures import world
    from mailmind.platform import settings
    import sqlite3
    import store
    d = tmp_path_factory.mktemp("world-template")
    import time_machine
    with settings.use(settings.Settings(home=str(d))), FakeIMAPServer() as srv, \
            time_machine.travel(NOW, tick=False):
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


# The two front doors every app test runs through: python3 server.py's and python -m mailmind serve's.
DOORS = ("old", "new")
_DOOR_IDS = tuple(f"door={d}" for d in DOORS)


@pytest.fixture(params=DOORS, ids=_DOOR_IDS)
def app(request, world, monkeypatch):
    """The app's HTTP server over this test's world, with the clock frozen at NOW and every
    module's process state reset, through each front door in turn. Yields an AppClient; its
    .clock moves the frozen time (clock.shift(timedelta(...)), clock.move_to(datetime))."""
    import dataclasses
    import time_machine
    import app_client
    import server
    from mailmind.bootstrap import legacy
    app_client.reset_state()
    monkeypatch.setattr(server, "CSRF", app_client.CSRF)
    legacy.wire(server)                 # as server.__main__ does, without starting the workers' threads
    from mailmind.platform import settings
    with time_machine.travel(NOW, tick=False) as clock:
        running = (app_client.RunningApp() if request.param == "old" else app_client.RunningAsgiApp()).start()
        try:
            with settings.use(dataclasses.replace(settings.current(), port=running.port)):
                client = app_client.AppClient(running.port)
                client.clock = clock
                yield client
                client.settle()
        finally:
            running.stop()
            app_client.reset_state()


def _without_door(nodeid):
    """The test's id without the front door it ran through: both doors answer to one golden file."""
    head, bracket, params = nodeid.partition("[")
    if not bracket:
        return nodeid
    kept = [p for p in params.removesuffix("]").split("-") if p not in _DOOR_IDS]
    return head + (f"[{'-'.join(kept)}]" if kept else "")


@pytest.fixture
def golden(request, world, app):
    """Record exchanges with golden.record / golden.note; they are compared with (or, under
    --update-golden, written to) the test's file in tests/golden/ when the test passes."""
    import golden as g
    rec = g.Golden(_without_door(request.node.nodeid), request.config.getoption("--update-golden"),
                   subs=[(str(world.dir), "<data>"), (f"127.0.0.1:{app.port}", "127.0.0.1:<app>"),
                         (f"localhost:{app.port}", "localhost:<app>")],
                   ports={world.server.port: "<imap>", world.smtp.port: "<smtp>", app.port: "<app>"})
    yield rec
    if request.node.stash.get(_CALL_PASSED, False):
        rec.verify()
