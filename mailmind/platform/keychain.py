"""The login Keychain, where MailMind keeps a secret it's given: the one module that reads or writes it.

A secret is a generic password whose service is `mailmind` and whose account is the secret's name, an item's name:
`database-key` for the key the database is encrypted with (mailmind.platform.db.key), `account:<address>` for an
account's password and `anthropic-api-key` for the Claude API key (mailmind.accounts.domain.credential names those two).

MailMind reaches the Keychain only through /usr/bin/security, which an item it adds trusts, so reading one back asks
nobody. A secret goes in on that program's standard input (`security -i`), never on its command line, where another
program could read it, and as hex (`-X`), so no character of it is read as the line's syntax. No secret, and nothing the
program says back, which can echo what it was handed, goes into a log or an error message. A name goes on a command
line, so it is letters, digits and `. _ % + @ : = -`, and starts with a letter or a digit.

`find-generic-password -w` gives back a secret that isn't printable ASCII as hex, which can't be told from a secret that
is hex, so a secret MailMind keeps is printable ASCII, as passwords and API keys are. `security -i` can end well when the
command it ran didn't, so what was kept is read back before a keep is done.

`use(backend)` puts another backend in the Keychain's place. The tests' guard does (tests/support/guard.py), so no test,
and no server a test starts, reaches the login Keychain.
"""
import re
import subprocess
import threading
from collections.abc import Callable
from typing import Any, Protocol

SERVICE = "mailmind"
SECURITY = "/usr/bin/security"
NOT_FOUND = 44                          # security's exit status for an item that isn't there
TIMEOUT = 60                            # seconds: an unlock prompt may be waiting for someone

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+@:=-]*")
_PRINTABLE = re.compile(r"[\x20-\x7e]+")


class KeychainProblem(RuntimeError):
    """The Keychain didn't do what was asked. Its text says what, and which item, and never holds a secret."""


class Unreachable(KeychainProblem):
    """There's no Keychain to ask: its program isn't there, or didn't answer in time. `why` is the error's class."""

    def __init__(self, why: str):
        super().__init__(f"MailMind keeps its secrets in the macOS Keychain, and couldn't ask it ({why}).")
        self.why = why


class Refused(KeychainProblem):
    """The Keychain's program answered, and said no: `status` is how it exited."""

    def __init__(self, said: str, status: int):
        super().__init__(f"{said} (security exited {status}). Is the login keychain locked?")
        self.status = status


class NotKept(KeychainProblem):
    """The Keychain was handed a secret, and holds another, or none."""

    def __init__(self, name: str):
        super().__init__(f"The Keychain wouldn't keep MailMind's item {name}.")


class Backend(Protocol):
    """What holds the items: the login Keychain, or a test's stand-in for it."""
    def find(self, name: str) -> str | None:
        """The secret kept under `name`, or None when there's none."""
        ...

    def add(self, name: str, secret: str, *, replace: bool) -> None:
        """Keep `secret` under `name`, in place of one already there only when `replace` says so."""
        ...

    def delete(self, name: str) -> bool:
        """Take the item `name` out. False when there was none."""
        ...


class Security:
    """The login Keychain, through /usr/bin/security. `run` is subprocess.run's stand-in, for a test."""

    def __init__(self, program: str = SECURITY, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run):
        self.program, self._run = program, run

    def _ask(self, args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        try:
            return self._run([self.program, *args], input=stdin, capture_output=True, text=True, timeout=TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Unreachable(type(e).__name__) from None

    def find(self, name: str) -> str | None:
        r = self._ask(["find-generic-password", "-s", SERVICE, "-a", name, "-w"])
        if r.returncode == NOT_FOUND:
            return None
        if r.returncode:
            raise Refused(f"The Keychain didn't give MailMind its item {name}", r.returncode)
        return r.stdout.removesuffix("\n")

    def add(self, name: str, secret: str, *, replace: bool) -> None:
        # What it says is let go: it can echo the line, and a keep is read back anyway.
        self._ask(["-i"], stdin=f"add-generic-password{' -U' if replace else ''} -s {SERVICE} -a {name} -X {secret.encode().hex()}\n")

    def delete(self, name: str) -> bool:
        r = self._ask(["delete-generic-password", "-s", SERVICE, "-a", name])
        if r.returncode == NOT_FOUND:
            return False
        if r.returncode:
            raise Refused(f"The Keychain didn't take MailMind's item {name} out", r.returncode)
        return True


_lock = threading.Lock()                # a keep and its reading back, one at a time
_backend: Backend = Security()


def use(backend: Backend) -> Backend:
    """Put `backend` in the login Keychain's place, and give back the one it replaced."""
    global _backend
    with _lock:
        before, _backend = _backend, backend
    return before


def _named(name: Any) -> str:
    if not (isinstance(name, str) and _NAME.fullmatch(name)):
        raise ValueError("A Keychain item MailMind keeps is named in letters, digits and . _ % + @ : = - alone.")
    return name


def _printable(secret: Any) -> str:
    if not (isinstance(secret, str) and _PRINTABLE.fullmatch(secret)):
        raise ValueError("A secret MailMind keeps in the Keychain is printable ASCII, and not empty: "
                         "letters, digits, punctuation and spaces.")
    return secret


def find(name: str) -> str | None:
    """The secret kept under `name`, or None when the Keychain holds none."""
    return _backend.find(_named(name))


def add(name: str, secret: str) -> str:
    """Keep `secret` under `name` unless the Keychain holds one there already, which this never replaces. The answer is
    what the Keychain holds there now, whoever put it there."""
    _named(name)
    _printable(secret)
    with _lock:
        _backend.add(name, secret, replace=False)
        held = _backend.find(name)
    if held is None:
        raise NotKept(name)
    return held


def put(name: str, secret: str) -> None:
    """Keep `secret` under `name`, in place of whatever the Keychain held there."""
    _named(name)
    _printable(secret)
    with _lock:
        _backend.add(name, secret, replace=True)
        held = _backend.find(name)
    if held != secret:
        raise NotKept(name)


def delete(name: str) -> bool:
    """Take the item `name` out of the Keychain. False when it held none."""
    return _backend.delete(_named(name))
