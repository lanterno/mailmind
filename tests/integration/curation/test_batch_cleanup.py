"""Cleaning up many senders at once through its routes, over the fake server: what the preview shows, the moves and the block
rules a batch leaves, and that one Undo takes it all back. What the use case decides is tests/unit/curation/test_batch_cleanup.py."""
import pytest

import problems
from fixtures import world as w
from mailmind.bootstrap.curation import rules

LOOM, THIMBLE = "news@loom.example.test", "orders@thimble.example.test"
NEWSLETTER, RECEIPT = "ada-inbox-03-newsletter.eml", "ada-inbox-05-receipt.eml"


def issues(world, n=2):
    for i in range(n):
        world.deliver(w.ADA, "INBOX", w.variant(NEWSLETTER, Message_ID=f"<issue-{300 + i}@loom.example.test>", Subject=f"Issue {300 + i}",
                                                Date=f"Fri, {i + 10:02} Oct 2026 07:00:00 +0000"))


def in_box(world, name):
    return [(acct, box) for acct, box, *_ in world.where(name)["server"]]


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


@pytest.mark.parametrize("path", ["/api/cleanup/batch", "/api/cleanup/batch/preview"])
@pytest.mark.parametrize("body", [{}, {"senders": []}, {"senders": ["nobody"]}])
def test_a_list_that_wont_read_is_the_400_and_nothing_is_decided(app, world, path, body):
    before = rules.load()
    r = app.post(path, body)
    assert (r.status, r.json["detail"]) == (400, "bad senders")
    assert rules.load() == before
