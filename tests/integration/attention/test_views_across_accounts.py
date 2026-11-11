"""On All inboxes the views that aren't folders read every account: one query, each row naming its account, and the rail's counts
over the same rows. All addresses are fictional."""
import json
from datetime import timedelta

import pytest

from fixtures import world as w
from mailmind.bootstrap.mailbox import store
from conftest import NOW

ADA_MAIL, BEA_MAIL, CY_MAIL = "ada-inbox-05-receipt.eml", "bea-inbox-01-client.eml", "cy-inbox-03-unrelated.eml"
ROW_FLAG = {"__snoozed__": None, "__muted__": "muted", "__waiting__": "waiting", "__replylater__": "replylater"}


def execute(sql, *args):
    c = store.conn()
    c.execute(sql, args)
    c.commit()
    c.close()


def across(app, folder, query=""):
    r = app.get(f"/api/messages?folder={folder}{query or '&limit=100'}")
    assert r.status == 200
    return r.json


def one_account(app, account, folder):
    return app.get(f"/api/messages?account={account}&folder={folder}&limit=100").json


def counts(app):
    r = app.get("/api/folders/across")
    assert r.status == 200
    return r.json


def snooze(app, world, name, hours):
    until = (NOW + timedelta(hours=hours)).isoformat()
    assert app.post("/api/snooze", {**world.copy(name), "until": until}).json["ok"]


def thread_of(world, name):
    c = store.conn()
    tid = c.execute("SELECT thread_id FROM messages WHERE pk=?", (world.pk(name),)).fetchone()[0]
    c.close()
    return tid


def follow_up(world, name, kind):
    account = world.placed[name][0]
    execute("INSERT OR REPLACE INTO followups(account, thread_id, kind, state, first_sent_ts, due_ts, created_at) "
            "VALUES (?,?,?,'due',1,2,'2026-11-01T00:00:00+00:00')", account, thread_of(world, name), kind)


def into_view(app, world, folder, name):
    """Put the message `name` into the view `folder`, as a person would, or for Waiting as the follow-up job does."""
    if folder == "__snoozed__":
        snooze(app, world, name, 24)
    elif folder == "__muted__":
        assert app.post("/api/mute", world.copy(name)).json["ok"]
    elif folder == "__replylater__":
        assert app.post("/api/replylater", {**world.copy(name), "on": True}).json["ok"]
    else:
        follow_up(world, name, "sent")


@pytest.mark.parametrize("folder", list(ROW_FLAG))
def test_with_no_account_named_a_view_lists_every_accounts_rows_each_naming_its_account(app, world, folder):
    into_view(app, world, folder, ADA_MAIL)
    into_view(app, world, folder, BEA_MAIL)
    into_view(app, world, folder, CY_MAIL)
    r = across(app, folder)
    assert sorted(i["account"] for i in r["items"]) == [w.ADA, w.BEA, w.CY]
    assert r["account"] is None and r["folder"] == folder and r["total"] == 3 and r["has_more"] is False
    if ROW_FLAG[folder]:
        assert all(i[ROW_FLAG[folder]] for i in r["items"])


@pytest.mark.parametrize("folder", list(ROW_FLAG))
def test_an_account_named_still_reads_that_account_alone(app, world, folder):
    into_view(app, world, folder, ADA_MAIL)
    into_view(app, world, folder, BEA_MAIL)
    r = one_account(app, w.ADA, folder)
    assert [i["account"] for i in r["items"]] == [w.ADA] and r["account"] == w.ADA and r["total"] == 1


def test_across_accounts_snoozed_mail_still_goes_soonest_wake_first(app, world):
    snooze(app, world, ADA_MAIL, 48)
    snooze(app, world, BEA_MAIL, 12)
    snooze(app, world, CY_MAIL, 24)
    assert [i["account"] for i in across(app, "__snoozed__")["items"]] == [w.BEA, w.CY, w.ADA]


def test_a_page_of_a_view_across_accounts_says_how_far_it_has_got(app, world):
    snooze(app, world, ADA_MAIL, 12)
    snooze(app, world, BEA_MAIL, 24)
    snooze(app, world, CY_MAIL, 36)
    first = across(app, "__snoozed__", "&limit=2")
    second = across(app, "__snoozed__", "&limit=2&offset=2")
    assert ([i["account"] for i in first["items"]], first["total"], first["has_more"]) == ([w.ADA, w.BEA], 3, True)
    assert ([i["account"] for i in second["items"]], second["has_more"]) == ([w.CY], False)


def test_the_rail_counts_each_view_over_every_account(app, world):
    for name in (ADA_MAIL, BEA_MAIL):
        into_view(app, world, "__snoozed__", name)
    into_view(app, world, "__muted__", CY_MAIL)
    follow_up(world, "ada-inbox-04-ci.eml", "sent")
    follow_up(world, "bea-inbox-02-security.eml", "received")
    into_view(app, world, "__replylater__", "cy-inbox-01-thread-start.eml")
    assert counts(app) == {"ok": True, "snoozed": 2, "muted": 1, "waiting": 2, "replylater": 1}


@pytest.mark.parametrize("folder, count", [("__muted__", "muted"), ("__waiting__", "waiting")])
def test_the_same_thread_id_in_two_accounts_is_two_threads(app, world, folder, count):
    """A thread id is an account's: mail sent to both accounts mints the same `rf:` id in each, and the rail must count both rows
    the view lists."""
    for name in (ADA_MAIL, BEA_MAIL):
        execute("UPDATE messages SET thread_id='rf:<shared@example.test>' WHERE pk=?", world.pk(name))
        if folder == "__muted__":
            execute("INSERT INTO muted(account, thread_id, muted_at) VALUES (?, 'rf:<shared@example.test>', '2026-11-01')",
                    world.placed[name][0])
        else:
            follow_up(world, name, "sent")
    assert across(app, folder)["total"] == 2 and counts(app)[count] == 2


def test_an_account_switched_off_leaves_the_views_and_their_counts(app, world):
    snooze(app, world, ADA_MAIL, 24)
    snooze(app, world, BEA_MAIL, 24)
    path = world.dir / "mailmind.config.json"
    config = json.loads(path.read_text())
    next(a for a in config["accounts"] if a["email"] == w.BEA)["disabled"] = True
    path.write_text(json.dumps(config))
    assert [i["account"] for i in across(app, "__snoozed__")["items"]] == [w.ADA]
    assert counts(app)["snoozed"] == 1


def test_a_real_folder_with_no_account_named_doesnt_become_every_accounts(app, world):
    r = app.get("/api/messages?folder=INBOX&limit=100").json
    assert r["items"] == [] and r["total"] == 0
