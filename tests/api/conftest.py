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
import pytest

import conftest
import served


@pytest.fixture(autouse=True)
def _keychain_shared_with_the_server(tmp_path_factory, monkeypatch):
    served.share_keychain(tmp_path_factory, monkeypatch)


@pytest.fixture
def app(request, world, tiny_build, tmp_path_factory):
    """The server, started over this test's world with its clock at NOW and serving the tiny build, stopped when the
    test ends."""
    with served.serving(request, world, tiny_build, tmp_path_factory, conftest.NOW, conftest._home) as server:
        yield server
