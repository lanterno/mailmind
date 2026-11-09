"""Opening a folder: a first scan that fails or runs long, older mail after warming, and when a folder is caught up."""
import gate
from fixtures import world as w
from mailmind.bootstrap import mailbox
from world_helpers import acct, late, wait_until


def test_hwf_a_folder_whose_first_sync_failed_is_synced_on_the_next_open(app, world):
    world.server.deliver(w.ADA, "Junk", late())
    world.server.fail("UID SEARCH", user=w.ADA)
    r = app.get(f"/api/messages?account={w.ADA}&folder=Junk").json
    assert (r["items"], r.get("sync_failed")) == ([], True)          # said, not shown as an empty folder
    r = app.get(f"/api/messages?account={w.ADA}&folder=Junk").json
    assert len(r["items"]) == 1 and "sync_failed" not in r


def test_hwf_opening_a_warmed_folder_shows_its_older_mail(app, world):
    from mailmind.bootstrap.mailbox import ingest
    for n in range(3):
        world.server.deliver(w.ADA, "Junk", late(n))
    ingest.sync_folder(acct(), "Junk", recent=2)                 # warm.warm_tick's first touch, WARM_RECENT=2
    assert len(app.get(f"/api/messages?account={w.ADA}&folder=Junk").json["items"]) == 3


def junk_scans(world):
    """How many times Ada's Junk has been scanned whole."""
    return sum(1 for c in world.server.commands_for(w.ADA, "UID SEARCH")
               if c["mailbox"] == "Junk" and c["args"] == ["ALL"])


def test_hwf_a_first_open_slower_than_the_wait_answers_then_lists_the_rest(app, world, monkeypatch):
    from gate import held_elsewhere
    monkeypatch.setattr(mailbox.folders, "FIRST_SYNC_WAIT", 0.2)
    world.server.deliver(w.ADA, "Junk", late())
    with held_elsewhere(w.ADA):                                  # a long mail operation holds the gate
        for _ in range(2):                                       # a second open joins the first's scan
            r = app.get(f"/api/messages?account={w.ADA}&folder=Junk").json
            assert (r["items"], r.get("syncing")) == ([], True)
    assert wait_until(lambda: not mailbox.folders._folder_syncs)
    r = app.get(f"/api/messages?account={w.ADA}&folder=Junk").json
    assert len(r["items"]) == 1 and "syncing" not in r
    assert junk_scans(world) == 1


def test_hwf_an_open_past_the_first_page_starts_no_scan(app, world):
    world.server.deliver(w.ADA, "Junk", late())
    assert app.get(f"/api/messages?account={w.ADA}&folder=Junk&offset=50").json["items"] == []
    assert junk_scans(world) == 0


def catch_ups(world, folder):
    """The searches above a folder's watermark: the catch-ups its opening ran."""
    return [c for c in world.server.commands_for(w.ADA, "UID SEARCH")
            if c["mailbox"] == folder and c["args"][:1] == ["UID"]]


def test_hwf_opening_a_folder_again_shows_mail_filed_there_elsewhere(app, world, monkeypatch):
    archive = f"/api/messages?account={w.ADA}&folder=Archive"
    before = len(app.get(archive).json["items"])
    world.server.deliver(w.ADA, "Archive", late())               # filed by another client
    monkeypatch.setattr(mailbox.folders, "CATCH_UP_EVERY", 0)
    r = app.get(archive).json
    assert len(r["items"]) == before + 1 and "catching_up" not in r


def test_hwf_a_folder_opened_again_soon_is_caught_up_once(app, world):
    for _ in range(3):
        app.get(f"/api/messages?account={w.ADA}&folder=Archive")
    assert len(catch_ups(world, "Archive")) == 1


def test_hwf_a_folder_opened_during_its_catch_up_says_so_too(app, world, monkeypatch):
    monkeypatch.setattr(mailbox.folders, "CATCH_UP_WAIT", 0.05)
    with gate.held_elsewhere(w.ADA):
        assert app.get(f"/api/messages?account={w.ADA}&folder=Archive").json["catching_up"]
        assert app.get(f"/api/messages?account={w.ADA}&folder=Archive").json["catching_up"]
    assert wait_until(lambda: not mailbox.folders._folder_syncs)
    assert "catching_up" not in app.get(f"/api/messages?account={w.ADA}&folder=Archive").json
    assert len(catch_ups(world, "Archive")) == 1


def test_hwf_opening_the_inbox_leaves_it_to_the_background_sync(app, world):
    app.get(f"/api/messages?account={w.ADA}")
    assert catch_ups(world, "INBOX") == []


def test_hwf_a_draft_saved_lets_drafts_catch_up_on_its_next_open(app, world):
    drafts = f"/api/messages?account={w.ADA}&folder=Drafts"
    app.get(drafts)
    assert app.post("/api/draft", {"account": w.ADA, "mode": "new", "to": ["lin@example.test"],
                                   "subject": "Swatch order", "text": "Two of each."}).json["ok"]
    app.get(drafts)
    assert len(catch_ups(world, "Drafts")) == 2


def test_hwf_a_slow_catch_up_answers_with_what_is_stored_then_says_what_came(app, world, monkeypatch):
    from gate import held_elsewhere
    archive = f"/api/messages?account={w.ADA}&folder=Archive"
    monkeypatch.setattr(mailbox.folders, "CATCH_UP_WAIT", 0.1)
    before = len(app.get(archive).json["items"])
    world.server.deliver(w.ADA, "Archive", late())
    monkeypatch.setattr(mailbox.folders, "CATCH_UP_EVERY", 0)
    with held_elsewhere(w.ADA):
        r = app.get(archive).json
        assert (len(r["items"]), r.get("catching_up")) == (before, True)
    assert wait_until(lambda: not mailbox.folders._folder_syncs)
    assert len(app.get(archive).json["items"]) == before + 1
