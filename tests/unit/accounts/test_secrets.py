"""Having a password from wherever its credential says (mailmind.accounts.adapters.secrets): a 1Password read, the
environment, or Proton Bridge's own sources. `op` never runs here: each test answers for it, as the program would."""
import subprocess
from types import SimpleNamespace

import pytest

from mailmind.accounts.adapters import secrets

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


def test_with_none_of_them_it_says_where_to_put_it_and_keeps_nothing(op):
    op({})
    with pytest.raises(RuntimeError, match="Proton Bridge password not configured. Set MAILMIND_PROTON_BRIDGE_PASS, "
                                           "or MAILMIND_PROTON_OP_ITEM"):
        secrets.bridge_password()
    secrets.BRIDGE_FILE.write_text("from-file\n")
    assert secrets.bridge_password() == "from-file"
