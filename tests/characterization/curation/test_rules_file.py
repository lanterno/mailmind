"""The rules file on disk: a bad edit, a write that fails, its mode and what is set aside."""
import json
import os

from fixtures import world as w
from mailmind.bootstrap import curation


def test_hwf_a_malformed_rules_file_does_not_stop_sync(world):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    with open(world.dir / "mailmind_rules.json", "w") as f:
        f.write('{"keep_senders": ["lin@example.test",]')
    world.server.deliver(w.ADA, "INBOX", w.variant("ada-inbox-02-lunch-confirm.eml", Message_ID="<late@example.test>"))
    assert ingest.sync_new(accounts.get(w.ADA))[0] == 1


def test_hwf_a_bad_edit_of_the_rules_keeps_the_rules_last_read(data_dir, capsys):
    from mailmind.bootstrap.curation import rules
    rules.add("lin@example.test", "keep")
    assert curation.classify("lin@example.test", "Win a prize now", "Lin", False)[2] == "kept by rule (sender)"
    with open(data_dir / "mailmind_rules.json", "w") as f:
        f.write('{"keep_senders": ["lin@example.test",]')         # a hand edit with a stray comma
    assert rules.load()["keep_senders"] == ["lin@example.test"]
    assert curation.classify("lin@example.test", "Win a prize now", "Lin", False)[2] == "kept by rule (sender)"
    rules.load()
    assert capsys.readouterr().out.count("can't be read") == 1     # said once, not at every read


def test_hwf_a_save_over_an_unreadable_rules_file_keeps_it_in_backups(data_dir):
    from mailmind.bootstrap.curation import rules
    bad = '{"keep_senders": ["lin@example.test",]'
    with open(data_dir / "mailmind_rules.json", "w") as f:
        f.write(bad)
    assert rules.load()["keep_senders"] == []                     # never read well: no rules apply
    rules.add("prizes.example.test", "block")
    assert rules.load()["block_domains"] == ["prizes.example.test"]
    [aside] = (data_dir / "backups").iterdir()
    assert aside.read_text() == bad and aside.stat().st_mode & 0o777 == 0o600


def test_hwf_the_rules_file_is_written_owner_only(data_dir):
    from mailmind.bootstrap.curation import rules
    old = os.umask(0o022)                                  # the usual umask, whatever the test runner's
    try:
        rules.add("lin@example.test", "keep")
    finally:
        os.umask(old)
    assert os.stat(data_dir / "mailmind_rules.json").st_mode & 0o777 == 0o600


def test_a_failed_rules_write_keeps_the_rules(app, world, monkeypatch):
    from mailmind.bootstrap.curation import rules
    assert app.post("/api/rules", {"token": "lin@example.test", "verdict": "keep"}).json["ok"]

    class DiskFull:
        load = staticmethod(json.load)

        @staticmethod
        def dump(obj, f, **kw):
            f.write('{"keep_senders": [')
            raise OSError("No space left on device")
    monkeypatch.setattr(rules, "json", DiskFull)
    assert app.post("/api/rules", {"token": "prizes.example.test", "verdict": "block"}).status == 500
    monkeypatch.setattr(rules, "json", json)
    r = app.get("/api/rules")
    assert r.status == 200
    assert r.json["rules"]["keep_senders"] == ["lin@example.test"]
