"""Sync's bookkeeping against the world's fake server: the per-folder watermark, the UIDVALIDITY
reset, ingest_message (Undo's one-message re-ingest), and the guards that keep reconcile from
pruning live mail. Reconcile prunes INBOX rows whose UID the server no longer has; every guard
here exists because a wrong prune deletes real mail from the local view, and incremental sync,
which only looks above the watermark, never brings it back.
"""
import pytest

from fixtures import world as w

NEW = "<late-1@example.test>"


def acct(email=w.ADA):
    import accounts
    return accounts.get(email)


def q(sql, *args):
    import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def watermark(folder="INBOX", email=w.ADA):
    [r] = q("SELECT last_uid, uidvalidity FROM sync_state WHERE account=? AND folder=?", email, folder)
    return r["last_uid"]


def inbox(email=w.ADA):
    return sorted((r["uid"], r["message_id"]) for r in q(
        "SELECT uid, message_id FROM messages WHERE account=? AND folder='INBOX'", email))


def late(**headers):
    return w.variant("ada-inbox-02-lunch-confirm.eml", Message_ID=NEW, Subject="Re: Lunch, a late word", **headers)


def searches(world, since):
    return [" ".join(map(str, c["args"])) for c in world.server.commands_for(w.ADA, "UID SEARCH")[since:]]


# ── the watermark ────────────────────────────────────────────────────────

def test_an_incremental_sync_asks_only_above_the_watermark_then_moves_it(world):
    import ingest
    wm = watermark()
    uid = world.server.deliver(w.ADA, "INBOX", late())
    n = len(world.server.commands_for(w.ADA, "UID SEARCH"))
    assert ingest.sync_new(acct())[0] == 1
    assert searches(world, n) == [f"UID {wm + 1}:*"]
    assert watermark() == uid > wm


def test_a_sync_with_nothing_new_leaves_the_watermark(world):
    import ingest
    wm = watermark()
    assert ingest.sync_new(acct())[0] == 0
    assert watermark() == wm


def test_ingest_message_stores_one_message_and_leaves_the_watermark(world):
    import ingest
    wm, uid = watermark(), world.server.deliver(w.ADA, "INBOX", late())
    assert ingest.ingest_message(acct(), "INBOX", NEW) == 1
    assert (uid, NEW) in inbox()
    assert watermark() == wm                          # mail below this new UID may not be stored yet
    ingest.sync_new(acct())                           # the next sync sees it again and stores it once
    assert [m for _, m in inbox()].count(NEW) == 1
    assert watermark() == uid


def test_ingest_message_into_a_folder_never_synced_marks_nothing_synced(world):
    import ingest
    assert not ingest.folder_synced(w.ADA, "Junk")         # the world never opened Ada's Junk
    world.server.deliver(w.ADA, "Junk", late())
    assert ingest.ingest_message(acct(), "Junk", NEW) == 1
    assert q("SELECT message_id FROM messages WHERE account=? AND folder='Junk'", w.ADA) == [{"message_id": NEW}]
    assert not ingest.folder_synced(w.ADA, "Junk")         # so its first full sync still runs


def test_ingest_message_for_a_message_the_server_lacks_stores_nothing(world):
    import ingest
    before = inbox()
    assert ingest.ingest_message(acct(), "INBOX", "<nowhere@example.test>") == 0
    assert ingest.ingest_message(acct(), "INBOX", "") == 0
    assert inbox() == before


# ── UIDVALIDITY ─────────────────────────────────────────────────────────

def test_a_uidvalidity_change_drops_the_folder_and_resyncs_it_whole(world):
    import ingest
    before = [m for _, m in inbox()]
    thread = {r["message_id"]: r["thread_id"] for r in q("SELECT message_id, thread_id FROM messages WHERE account=?",
                                                         w.ADA)}
    world.server.renumber(w.ADA, "INBOX")
    n = len(world.server.commands_for(w.ADA, "UID SEARCH"))
    ingest.sync_new(acct())
    assert searches(world, n) == ["ALL"]
    after = inbox()
    assert [u for u, _ in after] == list(range(1, len(after) + 1))
    assert sorted(m for _, m in after) == sorted(before)
    [r] = q("SELECT uidvalidity FROM sync_state WHERE account=? AND folder='INBOX'", w.ADA)
    assert r["uidvalidity"] == world.server.mailbox(w.ADA, "INBOX").uidvalidity
    assert watermark() == len(after)
    for r in q("SELECT message_id, thread_id FROM messages WHERE account=? AND folder='INBOX'", w.ADA):
        assert r["thread_id"] == thread[r["message_id"]]     # thread ids come from Message-IDs, not UIDs


# ── reconcile ────────────────────────────────────────────────────────────

def gone(world, name):
    """Another client deletes `name` from the server."""
    with world.server.lock:
        world.server.mailbox(*world.placed[name][:2]).messages.pop(world.uid(name))


def test_reconcile_prunes_what_the_server_lost_and_stamps_the_rest(world):
    import ingest
    gone(world, "ada-inbox-04-ci.eml")
    before = inbox()
    res = ingest.reconcile_account(acct())
    assert {k: res[k] for k in ("stored", "live", "ghosts", "pruned", "error")} == {
        "stored": len(before), "live": len(before) - 1, "ghosts": 1, "pruned": 1, "error": None}
    assert world.where("ada-inbox-04-ci.eml")["db"] == []
    assert {r["verified"] for r in q("SELECT verified FROM messages WHERE account=? AND folder='INBOX'", w.ADA)} == {1}


def test_a_dry_run_counts_the_ghosts_and_changes_nothing(world):
    import ingest
    gone(world, "ada-inbox-04-ci.eml")
    before = inbox()
    res = ingest.reconcile_account(acct(), dry_run=True)
    assert (res["ghosts"], res["pruned"]) == (1, 0)
    assert inbox() == before


def test_reconcile_never_lowers_the_watermark(world):
    import ingest
    uid = world.server.deliver(w.ADA, "INBOX", late())
    ingest.sync_new(acct())
    with world.server.lock:
        world.server.mailbox(w.ADA, "INBOX").messages.pop(uid)
    assert ingest.reconcile_account(acct())["pruned"] == 1
    assert watermark() == uid


@pytest.mark.parametrize("trouble", ["a refused SEARCH", "a dropped connection", "a refused EXAMINE"])
def test_reconcile_never_prunes_when_the_server_misbehaves(world, trouble):
    import ingest
    gone(world, "ada-inbox-04-ci.eml")
    before = inbox()
    if trouble == "a refused SEARCH":
        world.server.fail("UID SEARCH", user=w.ADA)
    elif trouble == "a dropped connection":
        world.server.drop("UID SEARCH", user=w.ADA)
    else:
        world.server.fail("EXAMINE", user=w.ADA)          # reconcile selects read-only
    res = ingest.reconcile_account(acct())
    assert res["error"] and res["pruned"] == 0
    assert inbox() == before


def test_reconcile_never_prunes_across_a_uidvalidity_change(world):
    import ingest
    before = inbox()
    world.server.renumber(w.ADA, "INBOX")
    res = ingest.reconcile_account(acct())
    assert "UIDVALIDITY changed" in res["error"] and res["pruned"] == 0
    assert inbox() == before


def test_an_empty_search_against_a_stored_inbox_prunes_only_when_forced(world):
    import ingest
    before = inbox()
    with world.server.lock:
        world.server.mailbox(w.ADA, "INBOX").messages.clear()
    res = ingest.reconcile_account(acct())
    assert res["error"].startswith("empty SEARCH") and res["pruned"] == 0
    assert inbox() == before
    assert ingest.reconcile_account(acct(), force=True)["pruned"] == len(before)
    assert inbox() == []


@pytest.mark.xfail(strict=True, reason="known bug: reconcile prunes mail a sync stored after its SEARCH")
def test_mail_a_sync_stores_while_reconcile_runs_survives_it(world, monkeypatch):
    import ingest
    import store
    real = store.reconcile

    def sync_lands_first(c, account, folder, live, when):
        world.deliver(w.ADA, "INBOX", late())       # the 300 s sync passes no IMAP gate
        return real(c, account, folder, live, when)

    monkeypatch.setattr(store, "reconcile", sync_lands_first)
    ingest.reconcile_account(acct())
    assert NEW in [m for _, m in inbox()]
