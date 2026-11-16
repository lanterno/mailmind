"""When an account's failed sync shows (mailmind.mailbox.domain.sync_health). Values only; test_http_sync_health.py holds
what the status pill and Settings read of it, on both servers."""
from mailmind.mailbox.domain.sync_health import Failing, and_one_more, shows


def test_trouble_that_may_pass_shows_on_the_second_failure_in_a_row_or_three_minutes_on():
    assert not shows(True, 1, 0)
    assert not shows(True, 1, 179)
    assert shows(True, 1, 180)
    assert shows(True, 2, 0)


def test_any_other_trouble_shows_at_once():
    assert shows(False, 1, 0)


def test_a_run_counts_its_failures_from_the_first():
    first = and_one_more(None, "ConnectionResetError", True, 1_000)
    assert first == Failing(1, 1_000, "ConnectionResetError", True)
    assert not first.shows(1_179) and first.shows(1_180)
    second = and_one_more(first, "TimeoutError", True, 1_060)
    assert second == Failing(2, 1_000, "TimeoutError", True)
    assert second.shows(1_060)
