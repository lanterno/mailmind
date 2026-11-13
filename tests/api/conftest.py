"""The API suite: what a MailMind server answers over HTTP, over the fictional world, its clock frozen at conftest.NOW.

These are the characterization suites made to run against any server. The server is a program the suite starts
(support/served.py): the Python one, or the one MAILMIND_TEST_SERVER names. A test reaches it over HTTP, as the page
does, and through the fakes it talks to, and records what it answered in the same golden files under tests/golden/, so a
server that answers as the Python one does passes unchanged. The world, the fakes and the golden files are this
process's; nothing a test asserts comes from inside the server.

`app` here is that program, where the characterization suites' `app` is the app in their own process: its .clock moves
the frozen time in both processes, .work(job, ...) runs one pass of a background job (no worker runs on its own),
.settle() waits for the work requests started, and .restart() stops it and starts it again over what it stored.
`world.deliver` puts a message on the fake server and has the server sync that folder.

A test may read the database file (its schema is the one tests/golden/schema_v*.sql pins, which a server in another
language keeps), and change it only while the server is stopped (`with app.stopped():`), as the world was made before
the server started. It never calls or patches a MailMind module to make the server do something: the server is another
process, which they never reach.
"""
import os

import pytest

import conftest
import fake_keychain
import served


@pytest.fixture(autouse=True)
def _keychain_shared_with_the_server(tmp_path_factory, monkeypatch):
    """The Keychain stand-in in a file the server reads too, named before the world puts the accounts' passwords in it."""
    monkeypatch.setenv(fake_keychain.VARIABLE, str(tmp_path_factory.mktemp("keychain") / "keychain.json"))


@pytest.fixture
def app(request, world, tiny_build, tmp_path_factory):
    """The server, started over this test's world with its clock at NOW, stopped when the test ends; what its own checks
    refused during the test fails it."""
    import time_machine
    if "ai" in request.fixturenames:
        request.getfixturevalue("ai")           # the key in the environment it starts with
    env = {**os.environ, "MAILMIND_HOME": str(world.dir), "MAILMIND_TEST_DIST": str(tiny_build),
           "MAILMIND_TEST_GUARDED": conftest._home, "PYTHONUNBUFFERED": "1"}
    log = tmp_path_factory.mktemp("server") / "server.log"
    with time_machine.travel(conftest.NOW, tick=False) as local:
        server = served.Served(env, conftest.NOW, local, log).start()
        world.sync = lambda account, folder: server.work("sync", account, folder)
        try:
            yield server
            server.settle()
            server.check()
        finally:
            server.stop()
