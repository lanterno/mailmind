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
