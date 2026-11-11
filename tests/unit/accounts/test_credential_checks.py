"""Reading every credential up front (Directory.preload, at the app's start) and `python -m mailmind check` (Directory.check):
each says how each account's went, and neither ever shows a secret."""
import pytest

from mailmind.accounts.adapters import config_file
from mailmind.accounts.application.directory import Directory

ADA, BEA, CY = "ada@example.test", "bea@example.test", "cy@example.test"
ACCOUNTS = [
    {"email": ADA, "type": "imap", "host": "imap.example.test", "credential": {"op": "op://Private/Ada/password"}},
    {"email": BEA, "type": "imap", "host": "imap.example.test", "credential": {"op": "op://Private/Bea/password"}},
    {"email": CY, "type": "imap", "host": "imap.example.test", "credential": {"env": "CY_PASSWORD"}},
]


class _Secrets:
    def __init__(self, refusing=()):
        self.refusing = set(refusing)
        self.asked = []

    def resolve(self, cred, *, who, strip=False):
        self.asked.append(who)
        if who in self.refusing:
            raise RuntimeError(f"op read failed for {who}")
        return "hunter22"

    def evict(self, cred):
        pass


@pytest.fixture
def directory(data_dir):
    def make(secrets, *, ai_key=None, **top):
        d = Directory(config_file.ConfigFile(), secrets, ai_key or (lambda: None))
        d.save({"accounts": [dict(a) for a in ACCOUNTS], **top})
        return d
    return make


def test_preload_reads_each_1password_credential_once_and_counts_those_it_had(directory):
    secrets = _Secrets()
    assert directory(secrets).preload() == 2
    assert secrets.asked == [ADA, BEA]


def test_a_preload_that_fails_is_said_withholding_the_account_and_the_rest_go_on(directory, capsys):
    secrets = _Secrets(refusing={ADA})
    assert directory(secrets).preload() == 1
    assert secrets.asked == [ADA, BEA]
    assert capsys.readouterr().out == "[accounts] <withheld>: a credential's preload failed: RuntimeError\n"


def test_check_says_ok_with_a_length_or_fail_with_why_for_each_account(directory, capsys):
    directory(_Secrets(refusing={BEA})).check()
    lines = capsys.readouterr().out.splitlines()
    assert [line.split() for line in lines] == [["ok", ADA, "(8", "chars)"], ["FAIL", BEA, "op", "read", "failed", "for", BEA],
                                                ["ok", CY, "(8", "chars)"]]


def test_check_says_how_the_claude_key_went_when_the_file_names_one(directory, capsys):
    directory(_Secrets(), ai_key=lambda: "sk-ant-hunter22", anthropic_api_key={"keychain": "anthropic-api-key", "version": 1}).check()
    assert capsys.readouterr().out.splitlines()[-1].split() == ["ok", "anthropic_api_key", "(15", "chars)"]


def test_check_says_why_the_claude_key_failed(directory, capsys):
    def refused():
        raise RuntimeError("env var ANTHROPIC_KEY not set for anthropic_api_key")
    directory(_Secrets(), ai_key=refused, anthropic_api_key={"env": "ANTHROPIC_KEY"}).check()
    assert capsys.readouterr().out.splitlines()[-1].split()[:3] == ["FAIL", "anthropic_api_key", "env"]


def test_check_never_shows_a_secret(directory, capsys):
    directory(_Secrets(refusing={BEA}), ai_key=lambda: "sk-ant-hunter22", anthropic_api_key={"keychain": "anthropic-api-key", "version": 1}).check()
    assert "hunter22" not in capsys.readouterr().out
