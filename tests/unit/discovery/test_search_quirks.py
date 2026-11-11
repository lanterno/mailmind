"""Three things decision D11 had fixed in search: a date means midnight where you are, snoozed mail
is out of sight until it wakes, and Search and Ask rank by one table of weights."""
import glob
import os
import time
from datetime import datetime, timezone

import pytest

from mailmind.bootstrap import discovery
from mailmind.kernel.snoozed import not_snoozed_sql
ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..")


@pytest.fixture
def in_new_york(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def bound(q):
    _, where, params = discovery.build(q)
    return where, params


# -- dates ----------------------------------------------------------------

def test_a_date_is_midnight_where_you_are(in_new_york):
    midnight = int(datetime(2026, 11, 2, 5, tzinfo=timezone.utc).timestamp())      # New York is at -05:00
    assert bound("after:2026-11-02") == (["m.ts >= ?"], [midnight])
    assert bound("before:2026-11-02") == (["m.ts < ?"], [midnight])


def test_daylight_saving_is_the_date_s_own(in_new_york):
    midnight = int(datetime(2026, 7, 1, 4, tzinfo=timezone.utc).timestamp())       # and at -04:00 in July
    assert bound("after:2026-07-01") == (["m.ts >= ?"], [midnight])


def test_a_zone_you_name_is_kept(in_new_york):
    assert bound("after:2026-11-02T00:00:00+00:00")[1] == [int(datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp())]


def test_with_utc_as_the_zone_nothing_moves():
    assert bound("after:2026-11-02")[1] == [int(datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp())]


@pytest.mark.parametrize("bad", ["after:2026-13-45", "before:soon", "after:0000-01-01"])
def test_a_date_that_isnt_one_bounds_nothing(bad):
    assert bound(bad) == ([], [])


def test_the_grammar_says_where_snoozed_mail_went():
    assert discovery.visible(["m.seen = ?"]) == ["m.seen = ?", not_snoozed_sql()]
    for asks in ("is:snoozed", "-is:snoozed"):
        where = discovery.build(asks)[1]
        assert discovery.visible(where) == where


# -- ranking --------------------------------------------------------------

def test_the_table_covers_every_indexed_column_and_ranks_them(data_dir):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    columns = [r[1] for r in c.execute("PRAGMA table_info(msg_fts)")]
    c.close()
    assert columns == ["subject", "from_name", "from_addr", "recipients", "snippet", "body_text"]
    weights = [float(x) for x in discovery.BM25.removeprefix("bm25(msg_fts,").removesuffix(")").split(",")]
    assert len(weights) == len(columns)
    by = dict(zip(columns, weights))
    assert by["subject"] > by["from_addr"] == by["from_name"] > by["body_text"] > by["recipients"] > by["snippet"]


def test_search_and_ask_rank_by_one_table():
    sources = [*glob.glob(os.path.join(ROOT, "*.py")), *glob.glob(os.path.join(ROOT, "mailmind", "**", "*.py"), recursive=True)]
    named = {os.path.basename(p) for p in sources if "bm25(" in open(p, encoding="utf-8").read()}
    assert named == {"fts.py"}
