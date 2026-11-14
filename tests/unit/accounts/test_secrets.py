"""Having a password from wherever its credential says (mailmind.accounts.adapters.secrets): the Keychain, a 1Password
read, the environment, or Proton Bridge's own sources, as the Rust server reads them too (core/crates/mm-accounts'
tests/bridge.rs) but for the file. `op` never runs here: each test answers for it, as the program
would, and the Keychain is the guard's stand-in."""
import subprocess
from types import SimpleNamespace

import pytest

from mailmind.accounts.adapters import secrets
from mailmind.platform import keychain

REF = "op://Private/Ada/password"
OTHER = "op://Private/Ada/old"


class _Op:
    """`op read`, answering each ref from its list of (returncode, stdout, stderr) in turn, the last for ever after."""

    def __init__(self, answers):
        self.answers = {ref: list(said) for ref, said in answers.items()}
        self.asked = []

    def run(self, cmd, capture_output, text):
        assert cmd[:2] == ["op", "read"] and capture_output and text
        self.asked.append(cmd[2])
        said = self.answers[cmd[2]]
        code, out, err = said.pop(0) if len(said) > 1 else said[0]
        return subprocess.CompletedProcess(cmd, code, out, err)


@pytest.fixture
def op(monkeypatch, tmp_path):
    """No cached read, no Bridge password from this machine, and `op` answered by the test."""
    monkeypatch.setattr(secrets, "_op_cache", {})
    monkeypatch.setattr(secrets, "_bridge", {})
    monkeypatch.setattr(secrets, "BRIDGE_FILE", tmp_path / "proton_bridge_pass")
    monkeypatch.delenv("MAILMIND_PROTON_BRIDGE_PASS", raising=False)
    monkeypatch.delenv("MAILMIND_PROTON_OP_ITEM", raising=False)
    slept = []
    monkeypatch.setattr(secrets, "time", SimpleNamespace(sleep=slept.append))

    def answering(answers):
        fake = _Op(answers)
        fake.slept = slept
        monkeypatch.setattr(secrets, "subprocess", SimpleNamespace(run=fake.run))
        return fake
    return answering


# -- 1Password ---------------------------------------------------------------------------------------------------

def test_a_read_is_what_op_printed_and_is_kept_for_the_process(op):
    fake = op({REF: [(0, "hunter2\n", "")]})
    assert secrets.resolve({"op": REF}, who="ada") == "hunter2"
    assert secrets.resolve({"op": REF}, who="ada") == "hunter2"
    assert fake.asked == [REF]


def test_strip_takes_the_spaces_out_of_an_app_password(op):
    op({REF: [(0, "abcd efgh ijkl mnop\n", "")]})
    assert secrets.resolve({"op": REF}, who="ada", strip=True) == "abcdefghijklmnop"


def test_a_refused_read_is_tried_once_and_raises_naming_the_ref(op):
    fake = op({REF: [(1, "", "[ERROR] item not found\n")]})
    with pytest.raises(RuntimeError, match=f"op read failed for {REF}: \\[ERROR\\] item not found"):
        secrets.resolve({"op": REF}, who="ada")
    assert fake.asked == [REF] and fake.slept == []


def test_a_failed_read_is_not_kept(op):
    fake = op({REF: [(1, "", "[ERROR] item not found"), (0, "hunter2", "")]})
    with pytest.raises(RuntimeError):
        secrets.resolve({"op": REF}, who="ada")
    assert secrets.resolve({"op": REF}, who="ada") == "hunter2"
    assert fake.asked == [REF, REF]


@pytest.mark.parametrize("why", ["connecting to desktop app: timeout", "authorization prompt dismissed"])
def test_a_timeout_or_an_unanswered_prompt_is_tried_again_after_a_pause(op, why):
    fake = op({REF: [(1, "", why), (0, "hunter2", "")]})
    assert secrets.resolve({"op": REF}, who="ada") == "hunter2"
    assert fake.asked == [REF, REF] and fake.slept == [3]


def test_it_is_tried_three_times_at_most_and_raises_what_op_said_last(op):
    fake = op({REF: [(1, "", "timeout 1"), (1, "", "timeout 2"), (1, "", "timeout 3")]})
    with pytest.raises(RuntimeError, match="timeout 3"):
        secrets.resolve({"op": REF}, who="ada")
    assert fake.asked == [REF] * 3


def test_each_ref_is_tried_in_turn_until_one_reads(op):
    fake = op({OTHER: [(1, "", "[ERROR] item not found")], REF: [(0, "hunter2", "")]})
    assert secrets.resolve({"op": [OTHER, REF]}, who="ada") == "hunter2"
    assert fake.asked == [OTHER, REF]


def test_when_no_ref_reads_the_last_refusal_is_raised(op):
    op({OTHER: [(1, "", "first")], REF: [(1, "", "second")]})
    with pytest.raises(RuntimeError, match="second"):
        secrets.resolve({"op": [OTHER, REF]}, who="ada")


@pytest.mark.xfail(strict=True, reason="_op_read hands op whatever the credential names, and op takes one that starts "
                                       "with a dash for an option: `--help` prints its help and ends well, and that "
                                       "is kept as the password. The Rust server sends only a reference (op://...).")
@pytest.mark.parametrize("named", ["--help", "-h"])
def test_what_isnt_a_reference_is_never_sent_to_op(op, named):
    fake = op({named: [(0, "op's help\n", "")]})
    with pytest.raises(RuntimeError):
        secrets.resolve({"op": named}, who="ada")
    assert fake.asked == []


# -- the Keychain ------------------------------------------------------------------------------------------------

ADA = "account:ada@example.test"


class _Counting:
    """The guard's stand-in, counting what is asked of it."""

    def __init__(self, held):
        self.held, self.asked = held, []

    def find(self, name):
        self.asked.append(name)
        return self.held.find(name)

    def add(self, name, secret, *, replace):
        self.held.add(name, secret, replace=replace)

    def delete(self, name):
        return self.held.delete(name)


@pytest.fixture
def counted(fake_keychain):
    from mailmind.platform import keychain
    counting = _Counting(fake_keychain)
    keychain.use(counting)
    yield counting
    keychain.use(fake_keychain)


def test_a_keychain_item_is_what_it_holds_as_kept_and_is_read_once_a_process(fake_keychain, counted):
    fake_keychain.add(ADA, " app password ", replace=False)
    assert secrets.resolve({"keychain": ADA}, who="ada") == " app password "
    assert secrets.resolve({"keychain": ADA}, who="ada", strip=True) == " app password "     # as it was typed
    assert counted.asked == [ADA]


def test_an_item_the_keychain_doesnt_hold_is_refused_naming_it_and_who_asked_and_nothing_is_kept(fake_keychain, counted):
    with pytest.raises(RuntimeError, match=f"the Keychain holds no item {ADA} for ada"):
        secrets.resolve({"keychain": ADA}, who="ada")
    fake_keychain.add(ADA, "app-password", replace=False)
    assert secrets.resolve({"keychain": ADA}, who="ada") == "app-password"
    assert counted.asked == [ADA, ADA]


@pytest.mark.parametrize("named", ["", "  ", None, 7])
def test_a_keychain_credential_naming_no_item_is_no_credential(named, counted):
    with pytest.raises(RuntimeError, match="no credential configured for ada"):
        secrets.resolve({"keychain": named}, who="ada")
    assert counted.asked == []


def test_the_database_key_is_no_credential(fake_keychain, counted):
    fake_keychain.add("database-key", "5e" * 32, replace=False)
    with pytest.raises(RuntimeError, match="no credential configured for ada"):
        secrets.resolve({"keychain": "database-key"}, who="ada")
    assert counted.asked == []


def test_a_keychain_that_wont_answer_is_said_and_nothing_is_kept(counted, monkeypatch):
    from mailmind.platform import keychain

    def locked(name):
        raise keychain.Refused(f"The Keychain didn't give MailMind its item {name}", 36)
    monkeypatch.setattr(counted, "find", locked)
    with pytest.raises(keychain.Refused, match="exited 36"):
        secrets.resolve({"keychain": ADA}, who="ada")
    assert secrets._keychain_cache == {}


def test_evict_and_forget_drop_what_was_read_so_the_next_read_asks_again(fake_keychain, counted):
    fake_keychain.add(ADA, "old", replace=False)
    fake_keychain.add("anthropic-api-key", "sk-ant-old", replace=False)
    secrets.resolve({"keychain": ADA}, who="ada")
    secrets.resolve({"keychain": "anthropic-api-key"}, who="anthropic_api_key")
    fake_keychain.add(ADA, "new", replace=True)
    fake_keychain.add("anthropic-api-key", "sk-ant-new", replace=True)
    secrets.evict({"keychain": ADA})
    assert secrets.resolve({"keychain": ADA}, who="ada") == "new"
    assert secrets.resolve({"keychain": "anthropic-api-key"}, who="anthropic_api_key") == "sk-ant-old"
    secrets.forget()
    assert secrets.resolve({"keychain": "anthropic-api-key"}, who="anthropic_api_key") == "sk-ant-new"


def test_a_new_version_of_an_item_is_read_again_as_another_process_kept_it(fake_keychain, counted):
    fake_keychain.add(ADA, "old", replace=False)
    assert secrets.resolve({"keychain": ADA, "version": 1}, who="ada") == "old"
    fake_keychain.add(ADA, "new", replace=True)                  # as `accounts password` does, then the file says version 2
    assert secrets.resolve({"keychain": ADA, "version": 1}, who="ada") == "old"
    assert secrets.resolve({"keychain": ADA, "version": 2}, who="ada") == "new"
    assert counted.asked == [ADA, ADA]


def test_keep_and_discard_change_the_keychain_and_forget_what_was_read(fake_keychain, counted):
    fake_keychain.add(ADA, "old", replace=False)
    secrets.resolve({"keychain": ADA, "version": 1}, who="ada")
    secrets.keep(ADA, "new")
    assert fake_keychain.items() == {ADA: "new"}
    assert secrets.resolve({"keychain": ADA, "version": 1}, who="ada") == "new"
    assert secrets.discard(ADA) is True and fake_keychain.items() == {}
    with pytest.raises(RuntimeError, match="holds no item"):
        secrets.resolve({"keychain": ADA, "version": 1}, who="ada")
    assert secrets.discard(ADA) is False


def test_each_assembly_starts_with_no_secret_read(fake_keychain, counted):
    from mailmind.bootstrap import wiring
    fake_keychain.add(ADA, "app-password", replace=False)
    secrets.resolve({"keychain": ADA}, who="ada")
    wiring.assemble()
    secrets.resolve({"keychain": ADA}, who="ada")
    assert counted.asked == [ADA, ADA]


# -- the environment ---------------------------------------------------------------------------------------------

def test_a_variable_that_isnt_set_is_refused_naming_it_and_who_asked(op, monkeypatch):
    monkeypatch.delenv("ADA_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="env var ADA_PASSWORD not set for ada"):
        secrets.resolve({"env": "ADA_PASSWORD"}, who="ada")


# -- Proton Bridge -----------------------------------------------------------------------------------------------

def test_the_variable_comes_first_and_is_kept(op, monkeypatch):
    fake = op({})
    monkeypatch.setenv("MAILMIND_PROTON_BRIDGE_PASS", "from-env")
    monkeypatch.setenv("MAILMIND_PROTON_OP_ITEM", "Bridge")
    secrets.BRIDGE_FILE.write_text("from-file\n")
    assert secrets.resolve({"proton_bridge": True}, who="ada") == "from-env"
    monkeypatch.delenv("MAILMIND_PROTON_BRIDGE_PASS")
    assert secrets.bridge_password() == "from-env"
    assert fake.asked == []


def test_then_the_1password_item_the_variable_names(op, monkeypatch):
    fake = op({"op://Private/Bridge/password": [(0, "from-op\n", "")]})
    monkeypatch.setenv("MAILMIND_PROTON_OP_ITEM", "Bridge")
    secrets.BRIDGE_FILE.write_text("from-file\n")
    assert secrets.bridge_password() == "from-op"
    assert fake.asked == ["op://Private/Bridge/password"]


def test_then_the_file_when_op_cant_read_the_item(op, monkeypatch):
    op({"op://Private/Bridge/password": [(1, "", "[ERROR] item not found")]})
    monkeypatch.setenv("MAILMIND_PROTON_OP_ITEM", "Bridge")
    secrets.BRIDGE_FILE.write_text("from-file\n")
    assert secrets.bridge_password() == "from-file"


def test_the_1password_item_comes_before_the_keychains(op, monkeypatch, fake_keychain):
    op({"op://Private/Bridge/password": [(0, "from-op\n", "")]})
    monkeypatch.setenv("MAILMIND_PROTON_OP_ITEM", "Bridge")
    fake_keychain.add("proton-bridge-password", "from-keychain", replace=True)
    assert secrets.bridge_password() == "from-op"


def test_then_the_keychains_item_before_the_file(op, monkeypatch, fake_keychain):
    fake = op({"op://Private/Bridge/password": [(1, "", "[ERROR] item not found")]})
    monkeypatch.setenv("MAILMIND_PROTON_OP_ITEM", "Bridge")
    fake_keychain.add("proton-bridge-password", "from-keychain", replace=True)
    secrets.BRIDGE_FILE.write_text("from-file\n")
    assert secrets.bridge_password() == "from-keychain"
    assert fake.asked == ["op://Private/Bridge/password"]


def test_the_file_when_the_keychain_cant_be_asked(op, monkeypatch):
    """A computer with no Keychain, or one locked: the file still answers, as it did before the Keychain was asked."""
    class Locked:
        def find(self, name):
            raise keychain.Unreachable("TimeoutExpired")
    op({})
    monkeypatch.setattr(keychain, "_backend", Locked())
    secrets.BRIDGE_FILE.write_text("from-file\n")
    assert secrets.bridge_password() == "from-file"


def test_with_none_of_them_it_says_where_to_put_it_and_keeps_nothing(op):
    op({})
    with pytest.raises(RuntimeError, match="Proton Bridge password not configured. Set MAILMIND_PROTON_BRIDGE_PASS, "
                                           "or MAILMIND_PROTON_OP_ITEM .*, or keep it in the login Keychain as "
                                           "MailMind's proton-bridge-password, or write it to"):
        secrets.bridge_password()
    secrets.BRIDGE_FILE.write_text("from-file\n")
    assert secrets.bridge_password() == "from-file"
