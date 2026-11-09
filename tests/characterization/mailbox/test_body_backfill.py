"""The body backfill: what it does with a body it can't fetch and a server it can't reach. Its pacing sleeps are stubbed out."""
import time

from mailmind.bootstrap import mailbox
from mailmind.mailbox.application import syncing as syncing_module
from mailmind.mailbox.domain import body_backfill
from stored_rows import q
from world_helpers import _Stop


class _Sleeps:
    """Stands in for the `time` module inside the Mailbox's Syncing: every sleep returns at once and is
    recorded, and the loop ends at the `stop_at`-th sleep of `8` seconds (the pause after a
    backfill cycle that found work)."""

    def __init__(self, stop_at):
        self.slept, self.stop_at = [], stop_at

    def sleep(self, s):
        self.slept.append(s)
        if self.slept.count(8) >= self.stop_at:
            raise _Stop()

    def __getattr__(self, name):
        return getattr(time, name)


def test_hwf_backfill_moves_past_bodies_it_cannot_fetch(world, monkeypatch):
    monkeypatch.setattr(syncing_module, "time", _Sleeps(stop_at=float("inf")))   # no real pacing between fetches
    monkeypatch.setattr(mailbox.syncing, "_backfill_missed", {})          # world alone resets no module state
    waiting = q("SELECT pk, account, folder, uid FROM messages WHERE body_text IS NULL ORDER BY ts DESC, pk DESC")
    stuck, older = waiting[:12], [r["pk"] for r in waiting[12:]]
    with world.server.lock:                                      # deleted elsewhere, not yet pruned here
        for r in stuck:
            world.server.mailbox(r["account"], r["folder"]).messages.pop(r["uid"])
    for _ in range(4):                                           # backfill rounds enough to have asked for each
        mailbox.syncing.backfill_round()
    fetched = q("SELECT pk FROM messages WHERE body_text IS NOT NULL")
    assert {r["pk"] for r in fetched} & set(older)
    missed = dict(mailbox.syncing._backfill_missed)
    assert set(missed) == {r["pk"] for r in stuck}               # each waits its turn, once
    assert all(one.until >= time.time() + body_backfill.NOT_GIVEN_WAIT - 60 for one in missed.values())


def test_hwf_backfill_asks_again_for_a_missed_body_once_its_wait_is_over(world, monkeypatch):
    monkeypatch.setattr(syncing_module, "time", _Sleeps(stop_at=float("inf")))
    monkeypatch.setattr(mailbox.syncing, "_backfill_missed", {})
    [newest] = q("SELECT pk, account, folder, uid FROM messages WHERE body_text IS NULL "
                 "ORDER BY ts DESC, pk DESC LIMIT 1")
    with world.server.lock:
        kept = world.server.mailbox(newest["account"], newest["folder"]).messages.pop(newest["uid"])
    mailbox.syncing.backfill_round()
    assert newest["pk"] in mailbox.syncing._backfill_missed
    with world.server.lock:                                      # it comes back, as a flaky server's would
        world.server.mailbox(newest["account"], newest["folder"]).messages[newest["uid"]] = kept
    mailbox.syncing._backfill_missed[newest["pk"]] = body_backfill.SetAside(0)    # its wait is over
    while q("SELECT 1 FROM messages WHERE pk=? AND body_text IS NULL", newest["pk"]):
        assert mailbox.syncing.backfill_round() == 8
    assert newest["pk"] not in mailbox.syncing._backfill_missed


def test_hwf_backfill_asks_nothing_more_of_a_server_it_cant_reach(world, monkeypatch):
    from mailmind.bootstrap.mailbox import ingest
    monkeypatch.setattr(syncing_module, "time", _Sleeps(stop_at=float("inf")))
    monkeypatch.setattr(mailbox.syncing, "_backfill_missed", {})
    monkeypatch.setattr(mailbox.syncing, "_backfill_resting", {})
    asked = []

    def down(pk):
        asked.append(pk)
        raise ConnectionRefusedError()
    monkeypatch.setattr(ingest, "fetch_body", down)
    assert mailbox.syncing.backfill_round() == body_backfill.UNREACHABLE_REST
    assert len(asked) == len(mailbox.syncing._backfill_resting) == 3      # once for each account: every server is down
    assert all(one.until <= time.time() + 60 for one in mailbox.syncing._backfill_missed.values())   # it may not be the message
    assert mailbox.syncing.backfill_round() == body_backfill.UNREACHABLE_REST
    assert len(asked) == 3
