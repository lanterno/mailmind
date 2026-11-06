"""Expiry rules: rule storage, sender/domain matching, and age selection. Nothing touches IMAP, and
the rules file is the test's own (`data_dir`). All addresses are fictional."""
import email
from datetime import datetime, timezone

import pytest

from mailmind.bootstrap import curation
from mailmind.bootstrap.curation import rules
from mailmind.curation.domain import rulebook
from mailmind.platform import settings

HOUR = 3600
NOW = 1_800_000_000


# ── matching ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("addr", ["bot@notify-example.test", "bot@mail.notify-example.test"])
def test_domain_matches_exact_and_subdomain(addr):
    assert rulebook.expiry_hours(addr, {"notify-example.test": 72}) == 72


@pytest.mark.parametrize("addr", ["bot@evilnotify-example.test", "notify-example.test@other.test"])
def test_domain_does_not_match_lookalike(addr):
    assert rulebook.expiry_hours(addr, {"notify-example.test": 72}) is None


@pytest.mark.parametrize("addr, hours", [("alerts@notify-example.test", 240), ("other@notify-example.test", 72)])
def test_sender_entry_wins_over_domain(addr, hours):
    assert rulebook.expiry_hours(addr, {"notify-example.test": 72, "alerts@notify-example.test": 240}) == hours


@pytest.mark.parametrize("addr", ["someone@person.test", ""])
def test_no_rule_no_expiry(addr):
    assert rulebook.expiry_hours(addr, {"notify-example.test": 72}) is None


# ── age selection ─────────────────────────────────────────────────────────────
def _row(addr, age_hours):
    return {"uid": "1", "from": addr, "ts": None if age_hours is None else NOW - age_hours * HOUR}


def test_selects_only_older_than_limit():
    rows = [_row("a@notify-example.test", 100), _row("b@notify-example.test", 10), _row("c@person.test", 1000)]
    hit = curation.select_expired(rows, {"notify-example.test": 72}, NOW)
    assert [r["from"] for r in hit] == ["a@notify-example.test"]
    assert hit[0]["hours"] == 72


def test_exactly_at_limit_is_kept():
    assert curation.select_expired([_row("a@notify-example.test", 72)], {"notify-example.test": 72}, NOW) == []


def test_unknown_age_never_expires():
    assert curation.select_expired([_row("a@notify-example.test", None)], {"notify-example.test": 72}, NOW) == []


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
def test_expiry_survives_keep_block_edits(data_dir):
    rules.set_expiry("Notify-Example.test", 72)
    rules.add("notify-example.test", "keep")
    rules.add("spam.test", "block")
    r = rules.load()
    assert r[rules.EXPIRE] == {"notify-example.test": 72}
    assert "notify-example.test" in r["keep_domains"]


def test_remove_leaves_expiry_alone(data_dir):
    rules.add("notify-example.test", "keep")
    rules.set_expiry("notify-example.test", 72)
    rules.remove("notify-example.test")
    r = rules.load()
    assert "notify-example.test" not in r["keep_domains"]
    assert r[rules.EXPIRE] == {"notify-example.test": 72}


def test_zero_hours_clears(data_dir):
    rules.set_expiry("notify-example.test", 72)
    rules.set_expiry("notify-example.test", 0)
    assert rules.load()[rules.EXPIRE] == {}


def test_legacy_file_without_expiry_loads(data_dir):
    with open(settings.current().rules, "w") as f:
        f.write('{"keep_domains": ["work.test"], "block_domains": [], '
                '"keep_senders": [], "block_senders": []}')
    r = rules.load()
    assert r[rules.EXPIRE] == {} and r["keep_domains"] == ["work.test"]


def test_missing_file_loads_empty(data_dir):
    assert rules.load()[rules.EXPIRE] == {}
