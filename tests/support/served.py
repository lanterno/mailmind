"""A MailMind server as the API suite (tests/api) meets it: a program it starts, on loopback, over a data directory the
suite made, that it reaches only over HTTP, as the page does, and through the line protocol every server under test speaks
on standard input and output (serve_python.py says what it is).

Nothing here imports MailMind. The program is the Python server's launcher unless MAILMIND_TEST_SERVER names another
command, so the same tests can be run against a server written in another language.
"""
import contextlib
import json
import os
import re
import select
import shlex
import subprocess
import sys
import time

from app_client import AppClient

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PYTHON_SERVER = [sys.executable, os.path.join(HERE, "serve_python.py")]
CSRF_META = re.compile(r'<meta name="csrf" content="([^"]+)"')


def command():
    """The program the suite runs: MAILMIND_TEST_SERVER, split as a shell would, or the Python server."""
    named = os.environ.get("MAILMIND_TEST_SERVER")
    return shlex.split(named) if named else PYTHON_SERVER


class ServerClock:
    """The served program's frozen clock, with this process's beside it (`local`, a time_machine traveller's coordinates),
    so the test's own sums of dates come out as the server's do."""

    def __init__(self, served, local, now):
        self._served = served
        self._local = local
        self.now = now

    def move_to(self, when):
        self._local.move_to(when)
        self.now = when
        self._served.ask("clock " + when.isoformat())

    def shift(self, delta):
        self.move_to(self.now + delta)


class Served(AppClient):
    """The program, started with `start` and stopped with `stop`; requests as AppClient sends them, with the CSRF token the
    page it served carries."""

    def __init__(self, env, now, local_clock, log_path):
        super().__init__(0)
        self.env = env
        self.log_path = log_path
        self.proc = None
        self.csrf = None
        self.clock = ServerClock(self, local_clock, now)

    def start(self, port=0):
        env = {**self.env, "MAILMIND_TEST_NOW": self.clock.now.isoformat(), "MAILMIND_TEST_PORT": str(port)}
        with open(self.log_path, "ab") as log:
            self.proc = subprocess.Popen(command(), cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=log, text=True, bufsize=1)
        ready = self._line(timeout=60)
        if not ready.startswith("ready "):
            raise AssertionError(f"the server didn't start: {ready!r}; its output is in {self.log_path}")
        self.port = int(ready.split()[1])
        self.origin = f"http://127.0.0.1:{self.port}"
        page = self.get("/")
        found = CSRF_META.search(page.body.decode("utf-8", "replace")) if isinstance(page.body, bytes) else None
        if found is None:
            raise AssertionError(f"the page at / carries no CSRF token (status {page.status})")
        self.csrf = found.group(1)
        return self

    def _line(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.proc.stdout], [], [], max(0.0, deadline - time.monotonic()))
            if ready:
                line = self.proc.stdout.readline()
                if not line:
                    raise AssertionError(f"the server ended (status {self.proc.wait()}); its output is in {self.log_path}")
                return line.rstrip("\n")
        raise AssertionError(f"the server said nothing for {timeout}s; its output is in {self.log_path}")

    def ask(self, command_line, timeout=60):
        """One command of the protocol; the server's one-line answer must be ok."""
        self.proc.stdin.write(command_line + "\n")
        self.proc.stdin.flush()
        answer = self._line(timeout)
        if answer != "ok":
            raise AssertionError(f"{command_line.split()[0]}: {answer}")

    def request(self, method, path, body=None, headers=None):
        # The token this server's page carries, where AppClient would send the in-process app's.
        return super().request(method, path, body, {"X-CSRF-Token": self.csrf, **(headers or {})})

    def work(self, job, *args):
        """One pass of a background job, as a worker would run it: outbox, commands, snoozes, follow-ups, or sync with
        the account and folder."""
        self.ask(f"work {job} {json.dumps(list(args))}", timeout=120)

    def settle(self, timeout=30):
        self.ask("settle", timeout=timeout + 30)

    def check(self):
        """What the server's own checks refused since the last check (a schema it broke, the tripwire) fails the test."""
        self.ask("check")

    def stop(self):
        if self.proc is None:
            return
        proc, self.proc = self.proc, None
        proc.stdin.close()
        try:
            proc.wait(30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise AssertionError(f"the server didn't stop when its input ended; its output is in {self.log_path}") from None
        finally:
            proc.stdout.close()

    def restart(self):
        """The server stops and starts again on the same port: only what it stored remains."""
        with self.stopped():
            pass

    @contextlib.contextmanager
    def stopped(self):
        """The server stopped while the test changes what it stores, as the world was made before it started, and
        started again over that on the same port. A running server is never written to behind its back."""
        port = self.port
        self.stop()
        try:
            yield
        finally:
            self.start(port)


@contextlib.contextmanager
def serving(request, world, dist, tmp_path_factory, now, guarded):
    """A suite's `app`: the server started over this test's world, serving the page built in `dist`, with its clock
    frozen at `now` and the world's folder syncs handed to it, then settled, checked and stopped as the test ends. What
    its own checks refused during the test fails the test, and its tripwire guards `guarded` as well as the repo root.
    A test that asks for `ai` has the key in the server's environment before it starts.

    `now` and `guarded` come from the suite's conftest, which imports tests/conftest.py as pytest collects it: imported
    here, during a test, it would run again and clear the environment the session set up."""
    import time_machine
    if "ai" in request.fixturenames:
        request.getfixturevalue("ai")
    env = {**os.environ, "MAILMIND_HOME": str(world.dir), "MAILMIND_TEST_DIST": str(dist),
           "MAILMIND_TEST_GUARDED": guarded, "PYTHONUNBUFFERED": "1"}
    log = tmp_path_factory.mktemp("server") / "server.log"
    with time_machine.travel(now, tick=False) as local:
        server = Served(env, now, local, log).start()
        world.sync = lambda account, folder: server.work("sync", account, folder)
        try:
            yield server
            server.settle()
            server.check()
        finally:
            server.stop()


def share_keychain(tmp_path_factory, monkeypatch):
    """The Keychain stand-in in a file the server reads too. A suite's autouse fixture names it, so it is named before
    the world puts the accounts' passwords in it."""
    import fake_keychain
    monkeypatch.setenv(fake_keychain.VARIABLE, str(tmp_path_factory.mktemp("keychain") / "keychain.json"))
