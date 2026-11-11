"""Cleaning up many senders at once: what it may name, what it shows before anyone says yes, and that one Undo takes it all back.
What the use case (mailmind.curation.application.cleanup.Cleanup) does with a batch is held on fakes behind Curation's ports."""
import pytest

from mailmind.curation.application.cleanup import Cleanup
from mailmind.curation.domain import cleanup_plan
from mailmind.kernel.errors import NotFound, Refused

LOOM, THIMBLE = "news@loom.example.test", "orders@thimble.example.test"


# ── what a batch may name ────────────────────────────────────────────────────────────────────────

def test_a_batch_names_each_sender_once_as_rows_go_by_them():
    assert cleanup_plan.senders_named([" News@Loom.example.test", "news@loom.example.test", THIMBLE]) == [LOOM, THIMBLE]


@pytest.mark.parametrize("raw", [None, "x@y.test", [], ["nobody"], [LOOM, 5], [f"a{i}@example.test" for i in range(cleanup_plan.BATCH_MAX + 1)]])
def test_a_list_that_wont_read_names_nobody(raw):
    assert cleanup_plan.senders_named(raw) is None


def test_a_batch_shares_its_moves_out_in_order_and_says_when_some_are_left():
    read = [("a", list(range(300)), False), ("b", list(range(300)), False), ("c", [1, 2], False)]
    taken, capped = cleanup_plan.within_batch(read)
    assert [(a, len(t)) for a, t in taken] == [("a", 300), ("b", 200), ("c", 0)]
    assert capped is True


def test_a_batch_within_every_cap_leaves_nothing():
    taken, capped = cleanup_plan.within_batch([("a", [1, 2], False), ("b", [3], False)])
    assert (dict(taken), capped) == ({"a": [1, 2], "b": [3]}, False)


def test_a_sender_past_their_own_cap_counts_as_left_behind():
    assert cleanup_plan.within_batch([("a", [1], True)])[1] is True


# ── the use case ─────────────────────────────────────────────────────────────────────────────────

def copy(uid, seen=1):
    """An Inbox copy as a sweep reads it, newest first, with the key and folder filing names it by."""
    return {"pk": uid, "message_key": 100 + uid, "uid": uid, "account": "ada@example.test", "folder": "INBOX",
            "subject": f"Issue {uid}", "seen": seen}


class World:
    """Every port Cleanup has, saying in one list what was asked of each, in order. Loom has three Inbox copies, one unread,
    and Thimble one."""

    def __init__(self):
        self.mail = {LOOM: [copy(1, seen=0), copy(2), copy(3)], THIMBLE: [copy(4)]}
        self.said = []
        self.unfiled = set()                 # uids filing leaves where they are
        self.trash_refuses = None            # what trash_many raises: none of them went

    # RuleBookRepository
    def add(self, token, verdict):
        self.said.append(("rule", token, verdict))
        return token, f"{verdict}_senders"

    # SenderMail
    def counts(self, addr):
        rows = self.mail.get(addr, [])
        return len(rows), sum(1 for r in rows if not r["seen"])

    def sweep_targets(self, addr, limit):
        return self.mail.get(addr, [])[:limit]

    # FilingCommands
    def undo_push(self, descriptor, group):
        self.said.append(("undo", descriptor["kind"], descriptor["token"], group))

    def trash_many(self, rows, undo_group):
        self.said.append(("trash", [r["uid"] for r in rows], undo_group))
        if self.trash_refuses:
            raise self.trash_refuses
        filed = [r for r in rows if r["uid"] not in self.unfiled]
        return {"count": len(filed), "failed": len(rows) - len(filed), "queued": 0, "filed": filed}

    def reclassify(self, token):
        self.said.append(("reclassify", token))
        return 0

    def stats_changed(self):
        self.said.append(("stats",))

    # AuditLog
    def cleaned_up(self, from_addr, token, swept, status):
        self.said.append(("audit", from_addr, swept, status))

    # Unsubscribing: a batch has no preview's token, so it never asks
    def resolve(self, from_addr):
        pytest.fail("a batch looked for a way to unsubscribe")

    def attempt(self, from_addr, resolved):
        pytest.fail("a batch sent an unsubscribe")

    def cleanup(self):
        return Cleanup(self, self, self, self, self, lambda: "this run's secret")


def test_a_preview_says_what_each_with_mail_would_come_to_and_does_nothing():
    world = World()
    out = world.cleanup().batch_preview({"senders": [LOOM, "nobody@example.test", THIMBLE]})
    assert out == {"ok": True, "senders": [{"from_addr": LOOM, "count": 3, "unseen": 1, "sweep_count": 3},
                                           {"from_addr": THIMBLE, "count": 1, "unseen": 0, "sweep_count": 1}],
                   "sweep_count": 4, "sweep_capped": False, "reversible": cleanup_plan.REVERSIBLE}
    assert world.said == []


def test_a_preview_of_nobody_with_mail_finds_no_sender():
    with pytest.raises(NotFound, match="no such sender"):
        World().cleanup().batch_preview({"senders": ["nobody@example.test"]})


@pytest.mark.parametrize("body", [{}, {"senders": []}, {"senders": ["nobody"]}])
def test_a_list_that_wont_read_is_refused_and_nothing_is_decided(body):
    world = World()
    for asked in (world.cleanup().batch_preview, world.cleanup().batch_execute):
        with pytest.raises(Refused, match="bad senders"):
            asked(body)
    assert world.said == []


def test_a_batch_blocks_each_then_sweeps_them_all_as_one_under_one_group():
    world = World()
    out = world.cleanup().batch_execute({"senders": [LOOM, THIMBLE], "undo_group": "ug-batch"})
    assert world.said == [
        ("rule", LOOM, "block"), ("undo", "unblock", LOOM, "ug-batch"),
        ("rule", THIMBLE, "block"), ("undo", "unblock", THIMBLE, "ug-batch"),
        ("trash", [1, 2, 3, 4], "ug-batch"),
        ("reclassify", LOOM), ("reclassify", THIMBLE), ("stats",),
        ("audit", LOOM, 3, "skipped"), ("audit", THIMBLE, 1, "skipped"),
    ]
    assert out == {"ok": True, "blocked": [LOOM, THIMBLE], "swept": 4, "sweep_failed": 0, "sweep_queued": 0,
                   "sweep_capped": False, "undo_group": "ug-batch", "reversible": cleanup_plan.REVERSIBLE}


def test_a_batch_never_unsubscribes():
    world = World()
    assert world.cleanup().batch_execute({"senders": [LOOM], "do_unsubscribe": True, "confirm_token": "1.x"})["ok"]


def test_the_audit_says_each_senders_own_sweep_as_filing_found_it():
    world = World()
    world.unfiled = {3}
    world.cleanup().batch_execute({"senders": [LOOM, THIMBLE]})
    assert [s for s in world.said if s[0] == "audit"] == [("audit", LOOM, 2, "skipped"), ("audit", THIMBLE, 1, "skipped")]


def test_a_batch_stops_at_its_cap_and_says_so(monkeypatch):
    monkeypatch.setattr(cleanup_plan, "BATCH_SWEEP_CAP", 2)
    world = World()
    out = world.cleanup().batch_execute({"senders": [LOOM, THIMBLE]})
    assert (out["blocked"], out["swept"], out["sweep_capped"]) == ([LOOM, THIMBLE], 2, True)
    assert [s[1] for s in world.said if s[0] == "trash"] == [[1, 2]]


def test_when_nothing_could_be_moved_the_blocks_stand_and_it_says_so():
    world = World()
    world.trash_refuses = RuntimeError("no")
    out = world.cleanup().batch_execute({"senders": [LOOM]})
    assert (out["swept"], out["sweep_failed"], out["sweep_queued"]) == (0, 3, 0)
    assert ("rule", LOOM, "block") in world.said and ("audit", LOOM, 0, "skipped") in world.said
