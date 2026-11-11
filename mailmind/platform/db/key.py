"""The key MailMind's database is encrypted with.

It is 32 random bytes, one for every MailMind database on this computer, kept in the login Keychain (the generic
password whose service is `mailmind` and account `database-key`). It is made once, by the first start, which makes a
new database. SQLCipher takes it as a raw key, so opening a connection stretches no passphrase: the key is random,
and there is nothing to stretch. A backup, the upgrade check's copy, an export of the database and a data directory
moved elsewhere all open with the same key.

MailMind never writes the key to a file, a log or the terminal. It reaches the Keychain through
mailmind.platform.keychain, which hands it a new key on standard input, never on a command line, where another program
could read it. It never replaces a key the Keychain holds: a database made with that key could no longer be read.

MAILMIND_DB_KEY (64 hex digits) stands in for the Keychain. The tests set it for themselves and every program they
start, the demo sets a key of its own, and a computer with no Keychain can set it. Set, the Keychain is never asked.
"""
import secrets
import threading
from collections.abc import Callable

from mailmind.platform import environment, keychain

SIZE = 32
ACCOUNT = "database-key"                # the item's name, under the Keychain's service for MailMind

_lock = threading.Lock()
_kept: bytes | None = None              # what the Keychain answered, once: each ask runs a program


class KeyProblem(RuntimeError):
    """The database's key can't be had: say so, and open nothing."""


def _from_hex(text: str, where: str) -> bytes:
    try:
        key = bytes.fromhex(text.strip())
    except ValueError:
        key = b""
    if len(key) != SIZE:
        raise KeyProblem(f"{where} holds no database key MailMind can use: it takes {SIZE * 2} hex digits.")
    return key


def _from_environment() -> bytes | None:
    raw = environment.DB_KEY.value()
    return _from_hex(raw, environment.DB_KEY.name) if raw else None


def _asking[T](ask: Callable[[], T]) -> T:
    """What the Keychain answers, or a KeyProblem that says what went wrong in the database key's words."""
    try:
        return ask()
    except keychain.Unreachable as e:
        raise KeyProblem(f"MailMind keeps the database key in the macOS Keychain, and couldn't ask it ({e.why}). "
                         f"On a computer with no Keychain, set {environment.DB_KEY.name}.") from None
    except keychain.Refused as e:
        raise KeyProblem(f"The Keychain didn't give MailMind its database key (security exited {e.status}). "
                         "Is the login keychain locked?") from None
    except keychain.NotKept:
        raise KeyProblem("MailMind made a database key, and the Keychain wouldn't keep it. Nothing was written.") from None


def _keychain() -> bytes | None:
    held = _asking(lambda: keychain.find(ACCOUNT))
    return None if held is None else _from_hex(held, "The Keychain's MailMind item")


def _keep(key: bytes) -> bytes:
    """Put `key` in the Keychain, unless it holds one already: never replace one. The answer is the key it holds now."""
    return _from_hex(_asking(lambda: keychain.add(ACCOUNT, key.hex())), "The Keychain's MailMind item")


def existing() -> bytes | None:
    """The key, or None when there is none yet."""
    global _kept
    from_env = _from_environment()
    if from_env is not None:
        return from_env
    with _lock:
        if _kept is None:
            _kept = _keychain()
        return _kept


def ensure() -> bytes:
    """The key, made and kept in the Keychain if there is none yet. Two processes making one at once end up with
    the same: the second finds the first's there, and takes it."""
    global _kept
    key = existing()
    if key is not None:
        return key
    with _lock:
        _kept = _keep(secrets.token_bytes(SIZE))    # what the Keychain holds now is the key, whoever put it there
        return _kept


def forget() -> None:
    """Ask the Keychain again next time (a test that changes what it answers)."""
    global _kept
    with _lock:
        _kept = None


def pragma(key: bytes) -> str:
    """The statement that keys a connection: a raw key, so SQLCipher derives nothing from it."""
    if len(key) != SIZE:
        raise KeyProblem(f"A database key is {SIZE} bytes.")
    return f"PRAGMA key = \"x'{key.hex()}'\""
