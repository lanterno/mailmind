"""Shared setup for the pytest-native tests in this directory.

Before any MailMind module loads, this takes any Claude key out of the environment, sets the database key
every test and every program it starts encrypts with (MAILMIND_DB_KEY, never the Keychain's), and installs
the tripwire in support/guard.py, which puts a stand-in in the login Keychain's place. It takes every variable `mailmind.platform.environment` declares out but
the three modes the suite passes under either way (MAILMIND_WORKERS, MAILMIND_POOL and
MAILMIND_WARM_FOLDERS), so every test starts in the full profile and one that wants another sets it. With MAILMIND_HOME gone, a test that forgets data_dir lands on the repo root, and the
tripwire guards the directory it named as it guards the repo root. Every test then fails if the tripwire refused anything during
it. The API itself is a local fake (fakes/anthropic_api.py) for the whole session; it refuses
every call that lacks its key, and the `ai` fixture supplies that key. Without it there is no
key, so AI is off and nothing reaches the fake.

`data_dir` makes a fresh temp directory the app's data directory (mailmind.platform.settings)
and creates the schema there. Tests that touch the store, config, rules, views or audit trails
must use it.
"""
import os
import time
from datetime import datetime, timezone

import pytest

import guard
from mailmind.platform import perimeter
from mailmind.platform import environment
from mailmind.platform.logging import stop as stop_logging
from mailmind.platform.db import database

_home = os.environ.get("MAILMIND_HOME", "")
# What a developer exported reaches no test, so each starts from the defaults. The modes the whole suite is meant to pass under
# either way are the exception, so `MAILMIND_WORKERS=account just test` runs it under a gate for each account.
_MODES = {environment.WORKERS.name, environment.POOL.name, environment.WARM_FOLDERS.name}
for _variable in environment.VARIABLES:
    if _variable.name not in _MODES:
        os.environ.pop(_variable.name, None)
os.environ["TZ"] = "UTC"            # "today" and local times must not depend on the machine
# The database key, for this process and every program a test starts: no test reaches the Keychain (the tripwire
# refuses its program), and each database a test makes is encrypted with this.
os.environ[environment.DB_KEY.name] = TEST_DB_KEY = "5e" * 32
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
                     help="also run the tests marked browser, which drive the page in Chromium")
    parser.addoption("--dovecot", action="store_true",
                     help="also run the tests marked dovecot, which need Docker to run Dovecot")


# The tests each run only when asked for: marker -> (option, how to ask).
_OPT_IN = {"browser": ("--browser", "just test-full"), "dovecot": ("--dovecot", "just test-dovecot")}


def pytest_collection_modifyitems(config, items):
    for marker, (option, recipe) in _OPT_IN.items():
        if config.getoption(option):
            continue
        skip = pytest.mark.skip(reason=f"{marker} test: run with {option} ({recipe})")
        for item in items:
            if item.get_closest_marker(marker):
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
    """The fake Claude API, there for every test run from this session
    (they inherit ANTHROPIC_BASE_URL). Without the `ai` fixture there is no key, so AI is off and
    nothing reaches it."""
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
def _fresh_app():
    """What the app holds between requests, made afresh for each test as a process starts with
    (mailmind.bootstrap.wiring.assemble): every context's objects new, imaplib for every account and
    nothing else offered, a cost governor that has seen no call and someone here now, no event kept,
    nothing subscribed. No test sees what another left, and none has to say which module keeps what."""
    from mailmind.bootstrap import wiring
    wiring.assemble()
    yield
    wiring.unwire()


@pytest.fixture(scope="session")
def tiny_build(tmp_path_factory):
    """The folder of a build small enough to write, which needs no node (fixtures/tiny_build.py)."""
    from fixtures import tiny_build as tiny
    return tiny.write(tmp_path_factory.mktemp("tiny-build"))


@pytest.fixture(autouse=True)
def _a_build(tiny_build, monkeypatch):
    """That build, whatever this machine has built, so `/` answers the same everywhere. A test that wants another, or none,
    sets `webroot.DIST` itself (`serve_the_build`, test_webroot.py)."""
    from mailmind.platform import webroot
    monkeypatch.setattr(webroot, "DIST", tiny_build)


@pytest.fixture(autouse=True)
def _no_log_file():
    """A test that started the app, or asked for the log file, leaves the next test's lines out of it."""
    yield
    stop_logging()


@pytest.fixture(autouse=True)
def fake_keychain():
    """The login Keychain as this test has it: the guard's stand-in (support/fake_keychain.py), empty as the test begins.
    `fake_keychain.items()` is what it holds."""
    guard.KEYCHAIN.clear()
    yield guard.KEYCHAIN
    guard.KEYCHAIN.clear()


@pytest.fixture(autouse=True)
def _tripwire():
    guard.violations.clear()
    yield
    found = list(guard.violations)
    guard.violations.clear()
    if found:
        pytest.fail("the test tripwire refused: " + "; ".join(found), pytrace=False)


@pytest.fixture(scope="session")
def _new_database(tmp_path_factory):
    """A database store.init made at the newest schema, once: running every migration took a test 40 ms."""
    from mailmind.platform import settings
    from mailmind.bootstrap.mailbox import store
    d = tmp_path_factory.mktemp("new-database")
    with settings.use(settings.Settings(home=str(d))):
        store.init()
    path = os.path.join(d, "mailmind.db")
    db = database.keyed(path)
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()
    return path


@pytest.fixture
def data_dir(tmp_path, _new_database):
    """A temp directory holding this test's database, config, rules, views and audit trails. The database is a copy of one
    at the newest schema, which store.init then finds as a start does."""
    import shutil
    from mailmind.platform import settings
    from mailmind.bootstrap.mailbox import store
    with settings.use(settings.Settings(home=str(tmp_path))):
        shutil.copyfile(_new_database, tmp_path / "mailmind.db")
        store.init()
        yield tmp_path


@pytest.fixture(scope="session")
def world_template(tmp_path_factory):
    """The world's database, built once by the app's own ingest. Tests get a copy via `world`."""
    from fakes.imap_server import FakeIMAPServer
    from fixtures import world
    from mailmind.platform import settings
    from mailmind.bootstrap.mailbox import store
    d = tmp_path_factory.mktemp("world-template")
    import time_machine
    with settings.use(settings.Settings(home=str(d))), FakeIMAPServer() as srv, \
            time_machine.travel(NOW, tick=False):
        placed = world.populate(srv)
        world.write_config(srv, str(d))
        store.init()
        world.ingest_all()
        db = database.keyed(os.path.join(d, "mailmind.db"))
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
    """The app as `python -m mailmind serve` builds it, over this test's world and the new set of objects `_fresh_app`
    assembled, with the clock frozen at NOW. Yields an AppClient; its .clock moves the frozen time (clock.shift(timedelta(...)),
    clock.move_to(datetime))."""
    import dataclasses
    import time_machine
    import app_client
    import schema_contract
    from mailmind.bootstrap import wiring
    schema_contract.watch_reads(monkeypatch)
    monkeypatch.setattr(perimeter, "CSRF", app_client.CSRF)
    wiring.wire()                 # as wiring.start does, without starting the workers' threads
    from mailmind.platform import settings
    with time_machine.travel(NOW, tick=False) as clock:
        from mailmind.bootstrap import assistant
        assistant.governor().forget()   # someone here at NOW, not at the real time the test began
        running = app_client.RunningAsgiApp().start()
        try:
            with settings.use(dataclasses.replace(settings.current(), port=running.port)):
                client = app_client.AppClient(running.port)
                client.clock = clock
                yield client
                client.settle()
                schema_contract.verify()
        finally:
            running.stop()


@pytest.fixture
def golden(request, world, app):
    """Record exchanges with golden.record / golden.note; they are compared with (or, under
    --update-golden, written to) the test's file in tests/golden/ when the test passes."""
    import golden as g
    from mailmind.platform import sse
    rec = g.Golden(request.node.nodeid, request.config.getoption("--update-golden"),
                   subs=[(str(world.dir), "<data>"), (f"127.0.0.1:{app.port}", "127.0.0.1:<app>"),
                         (f"localhost:{app.port}", "localhost:<app>"), (f"id: {sse.HUB.run}-", "id: <run>-"),
                         (f"127.0.0.1:{world.server.port}", "127.0.0.1:<imap>"),
                         (f"127.0.0.1:{world.smtp.port}", "127.0.0.1:<smtp>"),
                         (world.server.cert_sha256, "<imap certificate>"),
                         (world.smtp.cert_sha256, "<smtp certificate>")],
                   ports={world.server.port: "<imap>", world.smtp.port: "<smtp>", app.port: "<app>"})
    yield rec
    if request.node.stash.get(_CALL_PASSED, False):
        rec.verify()
