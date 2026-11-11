"""The database key (mailmind.platform.db.key): where it comes from, how it reaches the Keychain, and that one is
never replaced. The Keychain is a stand-in here: the tripwire refuses the real one's program."""
import subprocess

import pytest

from mailmind.platform import environment
from mailmind.platform.db import key

KEY = bytes(range(32))


class Keychain:
    """`security`, as far as key.py asks it: one generic password, and a record of each call."""

    def __init__(self, holds=None, fails=None):
        self.holds, self.fails, self.calls = holds, fails, []

    def __call__(self, args, stdin=None):
        self.calls.append((list(args), stdin))
        if self.fails is not None:
            return subprocess.CompletedProcess(args, self.fails, "", "")
        if args[0] == "find-generic-password":
            if self.holds is None:
                return subprocess.CompletedProcess(args, 44, "", "")
            return subprocess.CompletedProcess(args, 0, self.holds.hex() + "\n", "")
        if args == ["-i"] and stdin.startswith("add-generic-password ") and self.holds is None:
            self.holds = bytes.fromhex(stdin.split(" -w ")[1].split()[0])
        return subprocess.CompletedProcess(args, 0, "", "")


@pytest.fixture
def keychain(monkeypatch):
    """No MAILMIND_DB_KEY, and a Keychain of the test's own."""
    monkeypatch.delenv(environment.DB_KEY.name)
    fake = Keychain()
    monkeypatch.setattr(key, "_security", fake)
    key.forget()
    yield fake
    key.forget()


def test_the_variable_is_the_key_and_the_keychain_is_never_asked(monkeypatch):
    asked = []
    monkeypatch.setattr(key, "_security", lambda *a, **k: asked.append(a))
    monkeypatch.setenv(environment.DB_KEY.name, KEY.hex())
    assert key.existing() == key.ensure() == KEY
    assert asked == []


@pytest.mark.parametrize("value", ["not hex at all", "ab" * 31, "ab" * 33])
def test_a_variable_that_holds_no_key_names_itself_and_never_its_value(monkeypatch, value):
    monkeypatch.setenv(environment.DB_KEY.name, value)
    with pytest.raises(key.KeyProblem) as raised:
        key.existing()
    assert environment.DB_KEY.name in str(raised.value) and value not in str(raised.value)


def test_with_no_key_anywhere_there_is_none_and_nothing_is_made(keychain):
    assert key.existing() is None
    assert [args[0] for args, _ in keychain.calls] == ["find-generic-password"]


def test_the_keychains_key_is_read_once_a_process(keychain):
    keychain.holds = KEY
    assert key.existing() == key.existing() == key.ensure() == KEY
    assert len(keychain.calls) == 1


def test_a_new_key_goes_to_the_keychain_on_its_standard_input_and_never_on_a_command_line(keychain):
    made = key.ensure()
    assert len(made) == key.SIZE and keychain.holds == made
    adds = [(args, stdin) for args, stdin in keychain.calls if args == ["-i"]]
    assert len(adds) == 1
    _, stdin = adds[0]
    assert stdin.startswith("add-generic-password -s mailmind -a database-key -w ") and made.hex() in stdin
    assert all(made.hex() not in " ".join(args) for args, _ in keychain.calls)
    assert " -U" not in stdin                               # never told to replace one that is there


def test_a_key_another_process_kept_first_is_the_one_taken(keychain, monkeypatch):
    first = bytes(32)
    real = keychain.__call__

    def racing(args, stdin=None):
        if args == ["-i"] and keychain.holds is None:
            keychain.holds = first                          # another start kept its key just before this one
        return real(args, stdin)
    monkeypatch.setattr(key, "_security", racing)
    assert key.ensure() == first


@pytest.mark.parametrize("status", [36, 51, 128])
def test_a_keychain_that_wont_answer_opens_nothing_and_says_so(keychain, status):
    keychain.fails = status
    with pytest.raises(key.KeyProblem, match=f"exited {status}"):
        key.existing()


def test_a_keychain_that_wont_keep_a_new_key_says_so(keychain, monkeypatch):
    monkeypatch.setattr(key, "_keep", lambda made: None)    # it took the key, and holds none
    with pytest.raises(key.KeyProblem, match="wouldn't keep it"):
        key.ensure()


def test_no_keychain_at_all_points_at_the_variable(monkeypatch):
    monkeypatch.delenv(environment.DB_KEY.name)
    monkeypatch.setattr(key, "SECURITY", "/nonexistent/no-keychain-here")   # the tripwire refuses "security" by name
    key.forget()
    try:
        with pytest.raises(key.KeyProblem, match=environment.DB_KEY.name):
            key.existing()
    finally:
        key.forget()


def test_a_connection_is_keyed_with_the_raw_key_so_nothing_is_derived_from_it():
    assert key.pragma(KEY) == f"PRAGMA key = \"x'{KEY.hex()}'\""
    with pytest.raises(key.KeyProblem):
        key.pragma(KEY[:16])
