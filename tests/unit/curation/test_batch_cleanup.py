"""Cleaning up many senders at once: what it may name, what it shows before anyone says yes, and that one Undo takes it all back."""
import pytest

import problems
from fixtures import world as w
from mailmind.bootstrap import curation
from mailmind.bootstrap.curation import rules
from mailmind.curation.domain import cleanup_plan

LOOM, THIMBLE = "news@loom.example.test", "orders@thimble.example.test"
NEWSLETTER, RECEIPT = "ada-inbox-03-newsletter.eml", "ada-inbox-05-receipt.eml"


def issues(world, n=2):
    for i in range(n):
        world.deliver(w.ADA, "INBOX", w.variant(NEWSLETTER, Message_ID=f"<issue-{300 + i}@loom.example.test>", Subject=f"Issue {300 + i}",
                                                Date=f"Fri, {i + 10:02} Oct 2026 07:00:00 +0000"))


def in_box(world, name):
    return [(acct, box) for acct, box, *_ in world.where(name)["server"]]


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


# ── the preview ──────────────────────────────────────────────────────────────────────────────────

def test_the_preview_says_what_each_would_do_and_moves_nothing(app, world):
    issues(world)
    mark, before = world.mark(), rules.load()
    r = app.post("/api/cleanup/batch/preview", {"senders": [LOOM, THIMBLE]})
    assert (r.status, r.json["sweep_count"], r.json["sweep_capped"]) == (200, 4, False)
    assert [(s["from_addr"], s["count"], s["sweep_count"]) for s in r.json["senders"]] == [(LOOM, 3, 3), (THIMBLE, 1, 1)]
    assert r.json["reversible"] == {"block": True, "sweep": True, "unsubscribe": False}
    assert (world.writes(mark), rules.load()) == ([], before)


def test_a_preview_of_nobody_with_mail_is_a_404(app, world):
    r = app.post("/api/cleanup/batch/preview", {"senders": ["nobody@example.test"]})
    assert (r.status, r.json) == (404, problems.not_found("no such sender"))


# ── doing it ─────────────────────────────────────────────────────────────────────────────────────

def test_a_batch_blocks_and_sweeps_each_and_says_so(app, world):
    issues(world)
    r = app.post("/api/cleanup/batch", {"senders": [LOOM, THIMBLE], "undo_group": "ug-batch"})
    assert (r.status, r.json["blocked"], r.json["swept"], r.json["sweep_failed"], r.json["sweep_capped"]) == (200, [LOOM, THIMBLE], 4, 0, False)
    assert r.json["undo_group"] == "ug-batch"
    assert {LOOM, THIMBLE} <= set(rules.load()["block_senders"])
    assert in_box(world, NEWSLETTER) == [(w.ADA, "Trash")]
    assert in_box(world, RECEIPT) == [(w.ADA, "Trash")]


def test_one_undo_takes_back_the_whole_batch(app, world):
    issues(world)
    app.post("/api/cleanup/batch", {"senders": [LOOM, THIMBLE], "undo_group": "ug-batch"})
    r = app.post("/api/undo", {})
    assert (r.status, r.json["failed"]) == (200, 0)
    assert r.json["reversed"] == 6                       # four moves and two blocks
    assert not {LOOM, THIMBLE} & set(rules.load()["block_senders"])
    assert in_box(world, NEWSLETTER) == [(w.ADA, "INBOX")]
    assert in_box(world, RECEIPT) == [(w.ADA, "INBOX")]
    assert app.post("/api/undo", {}).status == 404


def test_a_batch_never_unsubscribes(app, world, monkeypatch):
    monkeypatch.setattr(curation.cleanup._unsub, "attempt", lambda *a: pytest.fail("a batch sent an unsubscribe"))
    assert app.post("/api/cleanup/batch", {"senders": [LOOM], "do_unsubscribe": True}).status == 200


def test_the_audit_says_each_senders_own_sweep(app, world):
    issues(world)
    app.post("/api/cleanup/batch", {"senders": [LOOM, THIMBLE]})
    said = {e["from"]: e for e in app.get("/api/audit").json["events"] if e["kind"] == "sender_cleanup"}
    assert (said[LOOM]["swept"], said[THIMBLE]["swept"]) == (3, 1)
    assert said[LOOM]["status"] == "skipped"


def test_a_batch_stops_at_its_cap_and_says_so(app, world, monkeypatch):
    issues(world, 3)
    monkeypatch.setattr(cleanup_plan, "BATCH_SWEEP_CAP", 2)
    r = app.post("/api/cleanup/batch", {"senders": [LOOM, THIMBLE]})
    assert (r.json["swept"], r.json["sweep_capped"]) == (2, True)
    assert in_box(world, RECEIPT) == [(w.ADA, "INBOX")]


@pytest.mark.parametrize("path", ["/api/cleanup/batch", "/api/cleanup/batch/preview"])
@pytest.mark.parametrize("body", [{}, {"senders": []}, {"senders": ["nobody"]}])
def test_a_list_that_wont_read_is_the_400_and_nothing_is_decided(app, world, path, body):
    before = rules.load()
    r = app.post(path, body)
    assert (r.status, r.json["detail"]) == (400, "bad senders")
    assert rules.load() == before


def test_when_nothing_could_be_moved_the_blocks_stand_and_it_says_so(app, world, monkeypatch):
    def refused(rows, group):
        raise RuntimeError("no")
    monkeypatch.setattr(curation._filing, "trash_many", refused)
    r = app.post("/api/cleanup/batch", {"senders": [LOOM]})
    assert (r.status, r.json["swept"], r.json["sweep_failed"]) == (200, 0, 1)
    assert LOOM in rules.load()["block_senders"]
