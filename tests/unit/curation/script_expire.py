"""Expiry rules: rule storage, sender/domain matching, and age selection. Hermetic — the
rules file is redirected to a temp path and nothing touches IMAP. All addresses are fictional.
Run: python3 test_expire.py"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, ROOT)
import email
import tempfile
from datetime import datetime, timezone

from mailmind.bootstrap.curation import rules
from mailmind.bootstrap import curation
from mailmind.platform import settings
from mailmind.curation.domain import rulebook

HOUR = 3600
NOW = 1_800_000_000


def _fresh_rules_file():
    settings.configure(settings.Settings(home=tempfile.mkdtemp()))


# ── matching ──────────────────────────────────────────────────────────────────
def test_domain_matches_exact_and_subdomain():
    exp = {"notify-example.test": 72}
    assert rulebook.expiry_hours("bot@notify-example.test", exp) == 72
    assert rulebook.expiry_hours("bot@mail.notify-example.test", exp) == 72

def test_domain_does_not_match_lookalike():
    exp = {"notify-example.test": 72}
    assert rulebook.expiry_hours("bot@evilnotify-example.test", exp) is None
    assert rulebook.expiry_hours("notify-example.test@other.test", exp) is None

def test_sender_entry_wins_over_domain():
    exp = {"notify-example.test": 72, "alerts@notify-example.test": 240}
    assert rulebook.expiry_hours("alerts@notify-example.test", exp) == 240
    assert rulebook.expiry_hours("other@notify-example.test", exp) == 72

def test_no_rule_no_expiry():
    assert rulebook.expiry_hours("someone@person.test", {"notify-example.test": 72}) is None
    assert rulebook.expiry_hours("", {"notify-example.test": 72}) is None


# ── age selection ─────────────────────────────────────────────────────────────
def _row(addr, age_hours):
    return {"uid": "1", "from": addr, "ts": None if age_hours is None else NOW - age_hours * HOUR}

def test_selects_only_older_than_limit():
    exp = {"notify-example.test": 72}
    rows = [_row("a@notify-example.test", 100), _row("b@notify-example.test", 10),
            _row("c@person.test", 1000)]
    hit = curation.select_expired(rows, exp, NOW)
    assert [r["from"] for r in hit] == ["a@notify-example.test"]
    assert hit[0]["hours"] == 72

def test_exactly_at_limit_is_kept():
    hit = curation.select_expired([_row("a@notify-example.test", 72)], {"notify-example.test": 72}, NOW)
    assert hit == []

def test_unknown_age_never_expires():
    hit = curation.select_expired([_row("a@notify-example.test", None)], {"notify-example.test": 72}, NOW)
    assert hit == []


# ── arrival time ──────────────────────────────────────────────────────────────
def test_arrival_prefers_internaldate():
    arrived = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    msg = email.message_from_string("Date: Thu, 01 Jan 2015 00:00:00 +0000\r\n\r\n")
    assert curation.arrival_ts(arrived, msg) == 1767268800

def test_arrival_falls_back_to_date_header():
    msg = email.message_from_string("Date: Thu, 01 Jan 2026 12:00:00 +0000\r\n\r\n")
    assert curation.arrival_ts(None, msg) == 1767268800

def test_arrival_unknown_is_none():
    msg = email.message_from_string("Subject: no date\r\n\r\n")
    assert curation.arrival_ts(None, msg) is None


# ── storage ───────────────────────────────────────────────────────────────────
def test_expiry_survives_keep_block_edits():
    _fresh_rules_file()
    rules.set_expiry("Notify-Example.test", 72)
    rules.add("notify-example.test", "keep")
    rules.add("spam.test", "block")
    r = rules.load()
    assert r[rules.EXPIRE] == {"notify-example.test": 72}
    assert "notify-example.test" in r["keep_domains"]

def test_remove_leaves_expiry_alone():
    _fresh_rules_file()
    rules.add("notify-example.test", "keep")
    rules.set_expiry("notify-example.test", 72)
    rules.remove("notify-example.test")
    r = rules.load()
    assert "notify-example.test" not in r["keep_domains"]
    assert r[rules.EXPIRE] == {"notify-example.test": 72}

def test_zero_hours_clears():
    _fresh_rules_file()
    rules.set_expiry("notify-example.test", 72)
    rules.set_expiry("notify-example.test", 0)
    assert rules.load()[rules.EXPIRE] == {}

def test_legacy_file_without_expiry_loads():
    _fresh_rules_file()
    with open(settings.current().rules, "w") as f:
        f.write('{"keep_domains": ["work.test"], "block_domains": [], '
                '"keep_senders": [], "block_senders": []}')
    r = rules.load()
    assert r[rules.EXPIRE] == {} and r["keep_domains"] == ["work.test"]

def test_missing_file_loads_empty():
    _fresh_rules_file()
    assert rules.load()[rules.EXPIRE] == {}


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", fn.__name__, repr(e))
        except Exception as e:
            failed += 1
            print("ER:U", fn.__name__, repr(e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    sys.exit(1 if failed else 0)
