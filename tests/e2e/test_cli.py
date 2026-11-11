"""`python -m mailmind`: every command the justfile names exists, and each does its work."""
import os
import pathlib
import re
import subprocess
import sys

import pytest

from fixtures import world as w
from mailmind.__main__ import main, parser

ROOT = pathlib.Path(__file__).resolve().parents[2]
SAMPLE = {"port": "8721", "target": "all", "days": "90", "limit": "10", "workers": "2", "tokens": "a.example.test",
          "token": "a.example.test", "hours": "72", "out": "/tmp/x", "args": ""}


def justfile_commands():
    text = (ROOT / "justfile").read_text(encoding="utf-8")
    found = re.findall(r"^\s+.*?\{\{py\}\} -m mailmind\s+(.*)$", text, flags=re.M)
    return [re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: SAMPLE[m.group(1)], line).split() for line in found]


def _with_nothing_installed(home, *args):
    """This Python without its site-packages, as `python3 -m mailmind` runs outside the project's environment."""
    return subprocess.run([sys.executable, "-S", "-E", *args], cwd=ROOT, env={**os.environ, "MAILMIND_HOME": str(home)}, capture_output=True, text=True)


@pytest.mark.parametrize("argv", [["serve"], ["assess", "10", "1"], ["assess", "--batch"], ["ai", "usage", "--days", "3"]], ids=" ".join)
def test_a_command_says_how_to_run_it_with_nothing_installed(tmp_path, argv):
    """Every command runs on the runtime packages: the Assistant's wiring loads the Anthropic SDK, say. Whichever is
    missing first, the command says so, and how to run it with them."""
    (tmp_path / "mailmind.db").touch()                          # so `ai usage` gets as far as the ledger
    done = _with_nothing_installed(tmp_path, "-m", "mailmind", *argv)
    assert done.returncode == 1, done.stderr
    said = re.fullmatch(r"MailMind needs its runtime packages, and ([\w.]+) isn't installed\. Run it as `(.*)`\.\n", done.stderr)
    assert said, done.stderr
    assert said[2] == "uv run python -m mailmind " + " ".join(argv)


def test_a_missing_module_of_mailminds_own_still_raises(monkeypatch):
    import mailmind.__main__ as entry

    def missing(args):
        raise ModuleNotFoundError("No module named 'mailmind.gone'", name="mailmind.gone")
    monkeypatch.setattr(entry, "_run", missing)
    with pytest.raises(ModuleNotFoundError):
        main(["check"])


def test_the_help_names_the_command_as_it_runs(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    assert capsys.readouterr().out.startswith("usage: uv run python -m mailmind ")


def test_the_justfile_names_commands():
    assert ["sync", "all", "90"] in justfile_commands()


@pytest.mark.parametrize("argv", justfile_commands(), ids=" ".join)
def test_every_command_the_justfile_names_parses(argv):
    parser().parse_args(argv)


def test_no_recipe_runs_a_script_at_the_root():
    text = (ROOT / "justfile").read_text(encoding="utf-8")
    scripts = re.findall(r"^\s+(?:\{\{py\}\}|python3?|uv run python) ([a-z_]+\.py)\b", text, flags=re.M)
    assert scripts == []


def test_check_says_ok_and_prints_no_secret(world, capsys):
    main(["check"])
    out = capsys.readouterr().out
    assert f"ok    {w.ADA}" in out
    assert "FAIL" not in out


def test_accounts_lists_them(world, capsys):
    main(["accounts"])
    assert w.ADA in capsys.readouterr().out


def test_sync_pulls_mail_and_says_how_much(world, capsys):
    world.deliver(w.ADA, "INBOX", w.variant("ada-inbox-05-receipt.eml", Message_ID="<cli-1@thimble.example.test>", Subject="By the command line"))
    main(["sync", w.ADA, "90"])
    out = capsys.readouterr().out
    assert w.ADA in out and "ERROR" not in out and "stats:" in out


def test_assess_says_ai_is_off_and_tries_nothing(data_dir, anthropic_api, monkeypatch, capsys):
    from mailmind.bootstrap import assistant
    monkeypatch.setattr(assistant.assessments, "summarize_one", lambda row: 1 / 0)
    main(["assess", "10", "1"])
    assert capsys.readouterr().out == "AI is off: there is no API key, so nothing was summarized.\n"
    assert anthropic_api.requests == []


@pytest.mark.parametrize("command", ["status", "inventory", "plan"])
def test_a_command_over_the_mailboxes_reads_them(world, capsys, command):
    before = world.mark()
    main([command, w.ADA])
    assert w.ADA in capsys.readouterr().out
    assert world.writes(before) == []


def test_a_dry_sweep_writes_nothing(world, capsys):
    before = world.mark()
    main(["sweep", w.ADA, "--dry"])
    capsys.readouterr()
    assert world.writes(before) == []


def test_a_dry_expiry_writes_nothing(world, capsys):
    main(["rules", "expire", "thimble.example.test", "1"])
    before = world.mark()
    main(["expire", w.ADA, "--dry"])
    capsys.readouterr()
    assert world.writes(before) == []


def test_rules_keep_block_remove_and_expire(data_dir, capsys):
    main(["rules", "keep", "Kept.example.test"])
    main(["rules", "block", "nobody@pests.example.test"])
    main(["rules", "expire", "shelf.example.test", "72"])
    main(["rules"])
    out = capsys.readouterr().out
    assert "kept.example.test -> keep_domains" in out
    assert "nobody@pests.example.test -> block_senders" in out
    assert "shelf.example.test -> expires after 72h" in out
    assert "keep_domains (1): kept.example.test" in out
    assert "expire_hours (1): shelf.example.test 72h" in out
    main(["rules", "remove", "kept.example.test"])
    main(["rules", "list"])
    assert "keep_domains (0): —" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["rules"], ["rules", "keep", "kept.example.test"], ["rules", "expire", "shelf.example.test", "72"]],
                         ids=" ".join)
def test_the_rules_commands_make_the_database_they_are_kept_in(data_dir, capsys, argv):
    """The rules live in the database, so a rules command run before the app ever has makes it, as a sync does."""
    for name in ("mailmind.db", "mailmind.db-wal", "mailmind.db-shm"):
        (data_dir / name).unlink(missing_ok=True)
    main(argv)
    main(["rules"])
    assert "keep_senders (0): —" in capsys.readouterr().out


def test_views_add_list_and_remove(data_dir, capsys):
    main(["views", "add", "Unread from Thimble", "from:thimble is:unread"])
    made = capsys.readouterr().out
    main(["views"])
    listing = capsys.readouterr().out
    assert "Unread from Thimble" in listing and "from:thimble is:unread" in listing
    view_id = re.search(r"'id': '([^']+)'", made).group(1)
    main(["views", "remove", view_id])
    assert capsys.readouterr().out == "removed\n"


def test_views_bring_a_database_from_before_them_up_to_date_first(tmp_path, capsys):
    """The views are in the database now, which a start brings to the newest schema: a view saved from the command line
    before MailMind has started since is kept all the same."""
    from mailmind.platform import settings
    from mailmind.platform.db import migrate, migrations
    with settings.use(settings.Settings(home=str(tmp_path))):
        migrate.init(settings.current().db, [m for m in migrations.MIGRATIONS if m[0] < 48])
        main(["views", "add", "Receipts", "is:receipt"])
        main(["views"])
    assert "Receipts" in capsys.readouterr().out


def test_export_plans_and_writes(world, tmp_path, capsys):
    main(["export"])
    assert "messages," in capsys.readouterr().out
    main(["export", "--format", "mbox", "--out", str(tmp_path), "--account", w.ADA])
    assert "mbox files under" in capsys.readouterr().out
    assert list(tmp_path.rglob("*.mbox"))


def test_db_rethread_regroups_the_mail_and_writes_down_what_it_names(world, capsys):
    from mailmind.bootstrap.mailbox import store
    reply, parent = world.pk("ada-inbox-02-lunch-confirm.eml"), world.pk("ada-inbox-01-lunch.eml")
    c = store.conn()
    try:
        c.execute("UPDATE messages SET thread_id='rf:broken' WHERE pk=?", (reply,))
        c.execute("DELETE FROM thread_links WHERE message_pk=?", (reply,))
        c.commit()
        main(["db", "rethread", w.ADA])
        assert w.ADA in capsys.readouterr().out
        ids = dict(map(tuple, c.execute("SELECT pk, thread_id FROM messages WHERE pk IN (?,?)", (reply, parent))))
        assert ids[reply] == ids[parent] != "rf:broken"
        assert c.execute("SELECT count(*) FROM thread_links WHERE message_pk=?", (reply,)).fetchone()[0] > 0
    finally:
        c.close()


def _config(world):
    import json
    return json.loads((world.dir / "mailmind.config.json").read_text())


def _typing(monkeypatch, text, *, tty=False):
    """What standard input gives: `text` as its first line, or as what the person types at a prompt."""
    import getpass
    import io
    stdin = io.StringIO(text + "\n")
    monkeypatch.setattr(stdin, "isatty", lambda: tty)
    monkeypatch.setattr(sys, "stdin", stdin)
    asked = []
    monkeypatch.setattr(getpass, "getpass", lambda prompt: asked.append(prompt) or text)
    return asked


def _exit(argv):
    with pytest.raises(SystemExit) as done:
        main(argv)
    return done.value.code


def test_accounts_password_keeps_it_in_the_keychain_and_points_the_config_at_it(world, monkeypatch, capsys, fake_keychain):
    _typing(monkeypatch, "  a-new-password  ")
    before = next(a for a in _config(world)["accounts"] if a["email"] == w.ADA)["credential"]
    assert _exit(["accounts", "password", w.ADA]) == 0
    assert fake_keychain.find(f"account:{w.ADA}") == "a-new-password"
    after = next(a for a in _config(world)["accounts"] if a["email"] == w.ADA)["credential"]
    assert after == {"keychain": f"account:{w.ADA}", "version": (before.get("version") or 0) + 1}
    out = capsys.readouterr().out
    assert out == f"{w.ADA}'s password is in the login Keychain as account:{w.ADA}, and mailmind.config.json names it.\n"
    assert "a-new-password" not in (world.dir / "mailmind.config.json").read_text()


def test_accounts_password_asks_at_a_prompt_that_shows_nothing_at_a_terminal(world, monkeypatch, capsys, fake_keychain):
    asked = _typing(monkeypatch, "typed-at-the-prompt", tty=True)
    assert _exit(["accounts", "password", w.ADA]) == 0
    assert asked == [f"Password for {w.ADA} (nothing shows as you type): "]
    assert fake_keychain.find(f"account:{w.ADA}") == "typed-at-the-prompt"
    assert "typed-at-the-prompt" not in capsys.readouterr().out


def test_accounts_password_for_an_account_the_config_doesnt_hold_changes_nothing(world, monkeypatch, capsys, fake_keychain):
    _typing(monkeypatch, "whatever")
    kept, before = fake_keychain.items(), _config(world)
    assert _exit(["accounts", "password", "nobody@example.test"]) == 1
    assert capsys.readouterr().out == ("mailmind.config.json has no account nobody@example.test: add it in Settings, or to the "
                                       "file, first.\n")
    assert (fake_keychain.items(), _config(world)) == (kept, before)
    assert _exit(["accounts", "password"]) == 1
    assert capsys.readouterr().out == "Name the account: uv run python -m mailmind accounts password you@example.com\n"


@pytest.mark.parametrize("typed, said", [
    ("", "Nothing was given, so nothing changed.\n"),
    ("café", "Nothing changed: A secret MailMind keeps in the Keychain is printable ASCII, and not empty: letters, digits, "
             "punctuation and spaces.\n"),
], ids=repr)
def test_a_password_that_cant_be_kept_changes_nothing_and_is_never_said(world, monkeypatch, capsys, fake_keychain, typed, said):
    _typing(monkeypatch, typed)
    kept, before = fake_keychain.items(), _config(world)
    assert _exit(["accounts", "password", w.ADA]) == 1
    assert capsys.readouterr().out == said
    assert (fake_keychain.items(), _config(world)) == (kept, before)


def test_a_keychain_that_wont_keep_it_changes_nothing(world, monkeypatch, capsys, fake_keychain):
    from mailmind.platform import keychain

    class Locked:
        def find(self, name):
            raise keychain.Refused(f"The Keychain didn't give MailMind its item {name}", 36)

        def add(self, name, secret, *, replace):
            pass
    _typing(monkeypatch, "a-new-password")
    before = _config(world)
    keychain.use(Locked())
    try:
        assert _exit(["accounts", "password", w.ADA]) == 1
    finally:
        keychain.use(fake_keychain)
    assert capsys.readouterr().out == "Nothing changed: The Keychain didn't keep the password. Is the login keychain locked?\n"
    assert _config(world) == before


def test_ai_key_keeps_the_claude_key_in_the_keychain_and_the_assistant_reads_it(data_dir, monkeypatch, capsys, fake_keychain):
    import json
    from mailmind.bootstrap import assistant
    (data_dir / "mailmind.config.json").write_text(json.dumps({"accounts": [], "anthropic_api_key": {"env": "SOME_KEY"}}))
    _typing(monkeypatch, "sk-ant-kept-in-the-keychain")
    assert _exit(["ai", "key"]) == 0
    assert capsys.readouterr().out == ("The Claude API key is in the login Keychain as anthropic-api-key, and "
                                       "mailmind.config.json names it.\n")
    assert fake_keychain.items() == {"anthropic-api-key": "sk-ant-kept-in-the-keychain"}
    written = (data_dir / "mailmind.config.json").read_text()
    assert json.loads(written)["anthropic_api_key"] == {"keychain": "anthropic-api-key", "version": 1}
    assert "sk-ant" not in written
    assert assistant.key() == ("sk-ant-kept-in-the-keychain", "mailmind.config.json")


def _plaintext_config(data_dir):
    import json
    raw = {"anthropic_api_key": "sk-ant-in-the-file", "accounts": [
        {"email": "ada@example.test", "type": "imap", "host": "imap.example.test", "credential": {"plain": "ada-in-the-file"}},
        {"email": "bea@example.test", "type": "imap", "host": "imap.example.test", "credential": "bea-in-the-file"},
        {"email": "cy@example.test", "type": "imap", "host": "imap.example.test", "credential": {"env": "CY_PASSWORD"}}]}
    (data_dir / "mailmind.config.json").write_text(json.dumps(raw))


def test_serve_refuses_a_config_that_holds_a_secret_itself_and_says_how_to_move_it(data_dir, monkeypatch):
    from mailmind.bootstrap import wiring
    _plaintext_config(data_dir)
    monkeypatch.setattr(wiring, "setup_logging", lambda: pytest.fail("the start went on past the config"))
    said = _exit(["serve", "0"])
    assert said == ("mailmind.config.json holds ada@example.test's password, bea@example.test's password and the Claude API "
                    "key in plain text, which MailMind no longer reads. Move them to the login Keychain with "
                    "`uv run python -m mailmind accounts move-plaintext`.")
    assert "in-the-file" not in said


def test_check_fails_an_account_whose_password_the_config_holds(data_dir, capsys):
    _plaintext_config(data_dir)
    main(["check"])
    out = capsys.readouterr().out
    assert ("FAIL  ada@example.test" in out and "ada@example.test's password in plain text" in out
            and "`uv run python -m mailmind accounts move-plaintext`" in out)
    assert "in-the-file" not in out


def test_move_plaintext_keeps_each_secret_the_config_holds_in_the_keychain(data_dir, capsys, fake_keychain):
    import json
    _plaintext_config(data_dir)
    assert _exit(["accounts", "move-plaintext"]) == 0
    assert capsys.readouterr().out == ("  moved ada@example.test\n  moved bea@example.test\n  moved anthropic_api_key\n"
                                       "done — 3 secret(s) now kept in the Keychain\n")
    assert fake_keychain.items() == {"account:ada@example.test": "ada-in-the-file", "account:bea@example.test": "bea-in-the-file",
                                     "anthropic-api-key": "sk-ant-in-the-file"}
    written = (data_dir / "mailmind.config.json").read_text()
    raw = json.loads(written)
    assert "in-the-file" not in written
    assert [a["credential"] for a in raw["accounts"]] == [{"keychain": "account:ada@example.test", "version": 1},
                                                          {"keychain": "account:bea@example.test", "version": 1},
                                                          {"env": "CY_PASSWORD"}]
    assert raw["anthropic_api_key"] == {"keychain": "anthropic-api-key", "version": 1}
    assert _exit(["accounts", "move-plaintext"]) == 0
    assert capsys.readouterr().out == "done — 0 secret(s) now kept in the Keychain\n"


def test_a_keychain_that_wont_answer_ends_a_command_with_its_line(monkeypatch):
    from mailmind.bootstrap import cli
    from mailmind.platform import keychain

    def refused():
        raise keychain.Refused("The Keychain didn't give MailMind its item account:ada@example.test", 36)
    monkeypatch.setattr(cli, "check", refused)
    assert _exit(["check"]) == ("The Keychain didn't give MailMind its item account:ada@example.test (security exited 36). "
                                "Is the login keychain locked?")
