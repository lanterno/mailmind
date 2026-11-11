"""The login Keychain (mailmind.platform.keychain): what it asks /usr/bin/security, that a secret goes only on standard input,
that no secret reaches an error, and what it refuses before it asks anything. `security` is a stand-in here: the tripwire
refuses the real one's program, and the guard's stand-in holds every other test's secrets."""
import subprocess

import pytest

import guard
from mailmind.platform import keychain

ADA = "account:ada@example.test"
SECRET = 'hunter2 "with" a \\ and \'quotes\''


class Security:
    """`/usr/bin/security` as far as the keychain module asks it: generic passwords under one service, and a record of each
    call. `fails` makes every call exit so; `echoes` makes each say back what it was handed, as a program may."""

    def __init__(self, fails=None, echoes=False):
        self.held, self.calls, self.fails, self.echoes = {}, [], fails, echoes

    def __call__(self, argv, input=None, **kwargs):
        assert argv[0] == keychain.SECURITY and kwargs["timeout"] == keychain.TIMEOUT
        args = argv[1:]
        self.calls.append((args, input))
        said = (input or "") + " ".join(args) if self.echoes else ""
        if self.fails is not None:
            return subprocess.CompletedProcess(argv, self.fails, said, said)
        if args[0] == "find-generic-password":
            name = args[args.index("-a") + 1]
            if name not in self.held:
                return subprocess.CompletedProcess(argv, 44, said, said)
            return subprocess.CompletedProcess(argv, 0, self.held[name] + "\n", said)
        if args[0] == "delete-generic-password":
            name = args[args.index("-a") + 1]
            return subprocess.CompletedProcess(argv, 0 if self.held.pop(name, None) is not None else 44, said, said)
        if args == ["-i"]:
            words = input.split()
            assert input.endswith("\n") and input.count("\n") == 1 and words[0] == "add-generic-password"
            name, data = words[words.index("-a") + 1], words[words.index("-X") + 1]
            assert words[words.index("-s") + 1] == keychain.SERVICE
            if "-U" in words or name not in self.held:
                self.held[name] = bytes.fromhex(data).decode()
            return subprocess.CompletedProcess(argv, 0, said, said)
        raise AssertionError(f"not asked of security: {args}")

    def command_lines(self):
        return [" ".join(args) for args, _ in self.calls]


@pytest.fixture
def security():
    """The keychain module on its real backend, which runs a stand-in for /usr/bin/security."""
    fake = Security()
    before = keychain.use(keychain.Security(run=fake))
    yield fake
    keychain.use(before)


def test_a_secret_goes_in_on_standard_input_as_hex_and_never_on_a_command_line(security):
    keychain.put(ADA, SECRET)
    assert keychain.find(ADA) == SECRET
    (args, line), = [(args, stdin) for args, stdin in security.calls if args == ["-i"]]
    assert line == f"add-generic-password -U -s mailmind -a {ADA} -X {SECRET.encode().hex()}\n"
    assert SECRET not in line                                       # its quotes and backslash are no syntax
    assert all(SECRET not in c and SECRET.encode().hex() not in c for c in security.command_lines())


def test_add_never_replaces_what_is_there_and_answers_with_what_is(security):
    security.held[ADA] = "kept-first"
    assert keychain.add(ADA, "a-new-one") == "kept-first"
    (line,) = [stdin for args, stdin in security.calls if args == ["-i"]]
    assert line.startswith(f"add-generic-password -s mailmind -a {ADA} -X ") and "-U" not in line
    del security.held[ADA]
    assert keychain.add(ADA, "a-new-one") == "a-new-one"


def test_put_replaces_what_is_there(security):
    security.held[ADA] = "the-old-one"
    keychain.put(ADA, "the-new-one")
    assert security.held == {ADA: "the-new-one"}


def test_find_gives_the_secret_as_kept_spaces_and_all_and_none_for_no_item(security):
    security.held[ADA] = "  spaced out  "
    assert keychain.find(ADA) == "  spaced out  "
    assert keychain.find("anthropic-api-key") is None
    assert security.command_lines()[-1] == "find-generic-password -s mailmind -a anthropic-api-key -w"


def test_delete_takes_the_item_out_and_says_whether_there_was_one(security):
    security.held[ADA] = SECRET
    assert keychain.delete(ADA) is True and security.held == {}
    assert keychain.delete(ADA) is False
    assert security.command_lines() == [f"delete-generic-password -s mailmind -a {ADA}"] * 2


@pytest.mark.parametrize("status", [36, 51, 128])
def test_a_keychain_that_says_no_says_how_it_exited_and_never_what_it_was_told(security, status):
    security.fails, security.echoes = status, True
    for asked in (lambda: keychain.find(ADA), lambda: keychain.delete(ADA), lambda: keychain.put(ADA, SECRET),
                  lambda: keychain.add(ADA, SECRET)):
        with pytest.raises(keychain.Refused) as raised:
            asked()
        assert raised.value.status == status and f"exited {status}" in str(raised.value)
        assert SECRET not in str(raised.value) and SECRET.encode().hex() not in str(raised.value)


def test_a_keep_the_keychain_didnt_take_says_so(security, monkeypatch):
    real = security.__call__

    def drops_adds(argv, input=None, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "", "") if argv[1:] == ["-i"] else real(argv, input, **kwargs)
    before = keychain.use(keychain.Security(run=drops_adds))
    try:
        with pytest.raises(keychain.NotKept, match=ADA):
            keychain.add(ADA, SECRET)
        security.held[ADA] = "another"
        with pytest.raises(keychain.NotKept) as raised:
            keychain.put(ADA, SECRET)                               # it holds one, and not the one handed it
        assert SECRET not in str(raised.value)
    finally:
        keychain.use(before)


@pytest.mark.parametrize("error", [FileNotFoundError(2, "No such file"), subprocess.TimeoutExpired("security", 60)])
def test_no_keychain_to_ask_says_so_by_the_errors_class(error):
    def raising(*args, **kwargs):
        raise error
    before = keychain.use(keychain.Security(run=raising))
    try:
        with pytest.raises(keychain.Unreachable, match=type(error).__name__) as raised:
            keychain.find(ADA)
        assert raised.value.why == type(error).__name__
    finally:
        keychain.use(before)


@pytest.mark.parametrize("name", ["", "has space", 'a"quote', "back\\slash", "-U", "line\nbreak", "café@example.test", None, 7])
def test_a_name_that_isnt_safe_on_a_command_line_is_refused_before_anything_is_asked(security, name):
    for asked in (lambda: keychain.find(name), lambda: keychain.put(name, "s"), lambda: keychain.add(name, "s"),
                  lambda: keychain.delete(name)):
        with pytest.raises(ValueError):
            asked()
    assert security.calls == []


@pytest.mark.parametrize("secret", ["", "line\nend", "tab\there", "café", "\x00", None, b"bytes"])
def test_a_secret_that_isnt_printable_ascii_is_refused_and_never_said(security, secret):
    for asked in (lambda: keychain.put(ADA, secret), lambda: keychain.add(ADA, secret)):
        with pytest.raises(ValueError) as raised:
            asked()
        assert not secret or str(secret) not in str(raised.value)
    assert security.calls == []


def test_use_puts_a_backend_in_place_and_gives_back_the_one_it_replaced(security):
    stand_in = guard.KEYCHAIN.__class__()
    before = keychain.use(stand_in)
    try:
        assert isinstance(before, keychain.Security)
        keychain.put(ADA, SECRET)
        assert stand_in.items() == {ADA: SECRET} and security.calls == []
    finally:
        assert keychain.use(before) is stand_in


def test_every_test_keeps_its_secrets_in_the_guards_stand_in(fake_keychain):
    assert keychain.use(fake_keychain) is fake_keychain is guard.KEYCHAIN
    keychain.put(ADA, SECRET)
    assert fake_keychain.items() == {ADA: SECRET}


def test_the_default_backend_is_the_login_keychain_through_security():
    made = keychain.Security()
    assert made.program == "/usr/bin/security" and made._run is subprocess.run
