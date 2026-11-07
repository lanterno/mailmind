"""What a sync of one folder asks the server for (mailmind.mailbox.domain.sync_plan): a value from what the select said and
what is stored. Nothing here reaches a server or a database; test_sync_state.py runs it against the fake."""
import pytest

from mailmind.mailbox.domain.sync_plan import FLAG_REFRESH, FlagPlan, plan_flags, plan_folder


def folder(*, uidvalidity=7, known=7, watermark=40, full=False, recent=None):
    return plan_folder(uidvalidity=uidvalidity, known_uidvalidity=known, watermark=watermark, full=full, recent=recent)


def test_an_incremental_sync_asks_for_what_is_above_the_watermark():
    plan = folder()
    assert plan.search == ("UID", "41:*") and not plan.whole and not plan.reset
    assert plan.read_flags                                   # and stored mail may have changed since


def test_a_range_above_the_last_uid_still_matches_the_last_and_that_is_no_new_mail():
    assert folder().to_fetch([40]) == ([], None)
    assert folder().to_fetch([40, 41, 45]) == ([41, 45], None)


@pytest.mark.parametrize("why", [{"watermark": 0}, {"full": True}, {"uidvalidity": 9}], ids=["first", "asked", "renumbered"])
def test_a_scan_asks_for_everything_and_reads_no_flags_again(why):
    plan = folder(**why)
    assert plan.search == ("ALL",) and plan.whole and not plan.read_flags
    assert plan.to_fetch([3, 4]) == ([3, 4], "synced")


def test_only_a_renumbered_folder_is_reset_first():
    assert folder(uidvalidity=9).reset
    assert not folder(full=True).reset and not folder(watermark=0).reset


@pytest.mark.parametrize("says, knows", [(None, 7), (7, None), (None, None), (7, 7)])
def test_a_uidvalidity_either_side_cant_compare_resets_nothing(says, knows):
    assert not folder(uidvalidity=says, known=knows).reset


def test_a_scan_bounded_to_the_newest_says_it_read_only_those():
    plan = folder(watermark=0, recent=2)
    assert plan.to_fetch([3, 4, 5]) == ([4, 5], "recent")
    assert plan.to_fetch([4, 5]) == ([4, 5], "synced")       # that is the whole folder
    assert plan.to_fetch([5]) == ([5], "synced")


def test_an_incremental_sync_ignores_the_bound_and_leaves_the_scan_state_be():
    assert folder(recent=1).to_fetch([41, 42, 43]) == ([41, 42, 43], None)


def test_with_no_modseq_the_newest_stored_are_read_and_no_mark_is_left():
    plan = plan_flags(None, None)
    assert plan == FlagPlan() and not plan.unchanged and plan.changed_since is None
    assert plan.mark(held_back=False, searched=False) is None
    assert plan_flags(0, 5) == FlagPlan()
    assert FLAG_REFRESH == 500


def test_a_modseq_matching_the_mark_means_nothing_to_read():
    assert plan_flags(9, 9).unchanged


def test_a_modseq_past_the_mark_asks_what_changed_since_it():
    plan = plan_flags(9, 5)
    assert plan.changed_since == 5 and not plan.unchanged
    assert plan.mark(held_back=False, searched=True) == 9


def test_a_refused_search_reads_the_newest_and_leaves_the_mark_behind():
    assert plan_flags(9, 5).mark(held_back=False, searched=False) is None


def test_what_a_read_skipped_keeps_the_mark_behind():
    assert plan_flags(9, 5).mark(held_back=True, searched=True) is None
    assert plan_flags(9, None).mark(held_back=True, searched=False) is None


@pytest.mark.parametrize("mark", [None, 12], ids=["no mark yet", "a mark past what the server says"])
def test_with_no_mark_to_go_by_the_newest_are_read_and_the_mark_is_set(mark):
    plan = plan_flags(9, mark)
    assert plan.changed_since is None and not plan.unchanged
    assert plan.mark(held_back=False, searched=False) == 9
