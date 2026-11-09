"""What a reconcile may take away (mailmind.mailbox.domain.prune): values in and values out. test_sync_state.py and
test_reconcile_through_the_pool.py run it against the fake."""
import pytest

from mailmind.mailbox.domain import prune


def test_a_folder_the_server_renumbered_is_not_pruned_and_says_why():
    assert prune.renumbered(9, 7) == "UIDVALIDITY changed (7->9) — prune skipped"


@pytest.mark.parametrize("server, stored", [(7, 7), (None, 7), (7, None), (None, None), (0, 7)])
def test_a_numbering_that_matches_or_that_a_side_doesnt_say_is_no_reason_to_stop(server, stored):
    assert prune.renumbered(server, stored) is None


def test_an_empty_search_against_stored_mail_prunes_nothing():
    assert prune.empty_search(set(), 3, force=False) == "empty SEARCH against non-empty store — skipped"


def test_a_person_forcing_it_prunes_on_an_empty_search():
    assert prune.empty_search(set(), 3, force=True) is None


@pytest.mark.parametrize("live, stored", [({1}, 3), (set(), 0)], ids=["found-some", "nothing-stored"])
def test_a_search_that_found_mail_or_a_folder_with_none_stored_is_no_reason_to_stop(live, stored):
    assert prune.empty_search(live, stored, force=False) is None


def test_ghosts_are_the_stored_uids_the_server_no_longer_holds():
    assert prune.ghosts([1, 2, 3], {2}) == 2
    assert prune.ghosts([1], {1}) == 0


def test_a_row_the_server_no_longer_holds_goes():
    assert prune.goes(1, {2}, None)


def test_a_live_row_stays():
    assert not prune.goes(2, {2}, None)


def test_a_row_stored_after_the_search_stays_though_the_search_never_saw_it():
    assert not prune.goes(9, {2}, among={1, 2})
    assert prune.goes(1, {2}, among={1, 2})


@pytest.mark.parametrize("interval, accounts, seconds", [(3600, 4, 900), (3600, 0, 3600), (60, 4, 30), (0, 1, 30)])
def test_the_rounds_spread_the_cycle_and_never_come_faster_than_the_floor(interval, accounts, seconds):
    assert prune.next_round(interval, accounts) == seconds
