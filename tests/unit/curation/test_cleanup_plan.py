"""What a cleanup is before anything happens, with no database and no I/O. What it does, in order, is
tests/characterization/test_http_cleanup.py."""
import pytest

from mailmind.curation.domain import cleanup_plan
from mailmind.curation.domain.cleanup_plan import SWEEP_CAP, SweepTally, plan_from, sender_named, tally, to_read, within_cap


@pytest.mark.parametrize("raw, addr", [("  Ada@Example.TEST ", "ada@example.test"), ("ada@example.test", "ada@example.test"),
                                       ("example.test", None), ("", None), ("   ", None), (None, None)])
def test_a_request_names_a_sender_by_address(raw, addr):
    assert sender_named(raw) == addr


# ── the block ─────────────────────────────────────────────────────────────────
def test_a_plan_with_no_address_is_no_plan():
    assert plan_from({"from_addr": "example.test", "block_scope": "domain"}) is None
    assert plan_from({}) is None


def test_a_plan_blocks_the_sender_unless_it_asks_for_the_domain():
    assert plan_from({"from_addr": "Ada@Example.test"}).block_token == "ada@example.test"
    assert plan_from({"from_addr": "ada@example.test", "block_scope": "everything"}).scope == "sender"
    domain = plan_from({"from_addr": "ada@sub.example.test", "block_scope": "domain"})
    assert (domain.scope, domain.block_token) == ("domain", "sub.example.test")


def test_a_plan_keeps_what_the_unsubscribe_needs():
    plan = plan_from({"from_addr": "ada@example.test"})
    assert (plan.do_unsubscribe, plan.confirm_token) == (False, "")
    plan = plan_from({"from_addr": "ada@example.test", "do_unsubscribe": 1, "confirm_token": "1.abc"})
    assert (plan.do_unsubscribe, plan.confirm_token) == (True, "1.abc")


def test_block_and_sweep_can_be_undone_and_the_unsubscribe_cannot():
    assert cleanup_plan.REVERSIBLE == {"block": True, "sweep": True, "unsubscribe": False}


# ── the cap ───────────────────────────────────────────────────────────────────
def test_it_reads_one_past_the_cap_to_know_whether_the_cap_was_hit():
    assert to_read() == SWEEP_CAP + 1


@pytest.mark.parametrize("read, kept, capped", [(0, 0, False), (SWEEP_CAP - 1, SWEEP_CAP - 1, False), (SWEEP_CAP, SWEEP_CAP, False),
                                                (SWEEP_CAP + 1, SWEEP_CAP, True)])
def test_the_sweep_takes_up_to_the_cap_and_says_when_it_left_some(read, kept, capped):
    taken, hit = within_cap(list(range(read)))
    assert (len(taken), hit) == (kept, capped)


# ── what it came to ───────────────────────────────────────────────────────────
def test_each_answer_counts_once():
    got = tally([{"ok": True}, {"ok": True, "status": "waiting"}, {"ok": True, "status": "queued"}, {"status": "queued"},
                 {"ok": False}, {}])
    assert got == SweepTally(moved=2, queued=2, failed=2)


def test_a_queued_message_counts_as_swept_and_a_failed_one_does_not():
    assert SweepTally(moved=2, queued=3, failed=4).swept == 5


def test_nothing_to_sweep_comes_to_nothing():
    assert tally([]) == SweepTally(0, 0, 0)
