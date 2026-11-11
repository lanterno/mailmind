"""Sweeps and expiry against a server that misleads or refuses: what they audit first, how they find their mail, what they prune."""
from datetime import timedelta

from fixtures import world as w
from mailmind.bootstrap import curation
from mailmind.curation.domain import rulebook
from stored_rows import q, rows
from world_helpers import RECEIPT, acct, boxes, gone


def test_hwf_expiry_takes_the_shortest_of_matching_domain_rules():
    expire = {"example.test": 72, "ci.example.test": 24, "noreply@ci.example.test": 240}
    assert rulebook.expiry_hours("bot@ci.example.test", expire) == 24
    assert rulebook.expiry_hours("bot@ci.example.test", {"example.test": 12, "ci.example.test": 24}) == 12
    assert rulebook.expiry_hours("noreply@ci.example.test", expire) == 240


def test_hwf_the_stored_inbox_plan_counts_the_junk_ingest_stored(world):
    from mailmind.bootstrap import accounts
    world.deliver(w.ADA, "INBOX", w.variant("ada-inbox-03-newsletter.eml", From="Lin Mori <lin@studio.example.test>",
                                            Message_ID="<issue-219@loom.example.test>"))
    [stored] = rows("SELECT count(*) AS n FROM messages WHERE account=? AND folder='INBOX' AND is_junk=1", w.ADA)
    assert stored["n"] == 4
    assert curation.sweeps.plan_stored(accounts.get(w.ADA))["junk"] == stored["n"]


def test_hwf_the_stored_inbox_plan_leaves_out_mail_on_its_way_out(world):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import store
    before = curation.sweeps.plan_stored(accounts.get(w.ADA))
    c = store.conn()
    c.execute("UPDATE messages SET pending_removal=1 WHERE pk=?", (world.pk("ada-inbox-03-newsletter.eml"),))
    c.commit()
    c.close()
    assert curation.sweeps.plan_stored(accounts.get(w.ADA))["in_inbox"] == before["in_inbox"] - 1


JUNK_INBOX = ("ada-inbox-03-newsletter.eml", "ada-inbox-04-ci.eml", "ada-inbox-12-scam.eml")


def audit(world):
    """The sweep trail, in the order it was written."""
    import json
    return [json.loads(r["body"]) for r in q("SELECT body FROM audit_events WHERE trail='sweep' ORDER BY id")]


def test_hwf_a_sweep_retries_a_move_the_server_said_ok_to_but_didnt_do(world):
    world.server.fail("UID MOVE", "OK [scripted] moved, or so it says", user=w.ADA)
    res = curation.sweeps.sweep(acct())
    assert (res["moved"], res["stragglers"], res["error"]) == (3, [], None)
    for name in JUNK_INBOX:
        assert boxes(world, name) == ([(w.ADA, "Trash")], []), name


def test_hwf_a_sweep_reports_and_keeps_mail_that_never_moved(world):
    uid = str(world.uid("ada-inbox-04-ci.eml"))
    world.server.fail("UID MOVE", "OK [scripted] moved, or so it says", times=None, user=w.ADA,
                      when=lambda a: uid in str(a[0]).split(","))
    res = curation.sweeps.sweep(acct())
    assert (res["moved"], res["stragglers"]) == (2, [uid])
    assert boxes(world, "ada-inbox-04-ci.eml") == ([(w.ADA, "INBOX")], [(w.ADA, "INBOX")])
    assert boxes(world, "ada-inbox-12-scam.eml") == ([(w.ADA, "Trash")], [])


def test_hwf_a_sweep_prunes_what_it_moved_by_message_id(world):
    """UIDVALIDITY changed and no sync has seen it, so a stored UID may now be another message's."""
    from mailmind.bootstrap.mailbox import store
    swept, kept = world.row("ada-inbox-04-ci.eml"), world.row("ada-inbox-01-lunch.eml")
    c = store.conn()
    c.execute("UPDATE messages SET uid=90001 WHERE pk=?", (swept["pk"],))
    c.execute("UPDATE messages SET uid=? WHERE pk=?", (world.uid("ada-inbox-04-ci.eml"), kept["pk"]))
    c.commit()
    c.close()
    assert curation.sweeps.sweep(acct())["moved"] == 3
    assert q("SELECT pk FROM messages WHERE pk=?", swept["pk"]) == []
    assert q("SELECT pk FROM messages WHERE pk=?", kept["pk"]) == [{"pk": kept["pk"]}]


def test_hwf_a_sweep_prunes_a_message_with_no_message_id_by_uid(world):
    uid = world.deliver(w.ADA, "INBOX", b"From: News <news@loom.example.test>\r\nTo: ada@example.test\r\n"
                                        b"Subject: No id\r\nList-Unsubscribe: <https://loom.example.test/u>\r\n"
                                        b"Date: Mon, 02 Nov 2026 10:00:00 +0000\r\n\r\nHello\r\n")
    assert curation.sweeps.sweep(acct())["moved"] == 4
    assert q("SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.ADA, uid) == []


def test_hwf_a_sweep_audits_before_it_moves(world):
    world.server.drop("UID MOVE", user=w.ADA)
    res = curation.sweeps.sweep(acct())
    assert res["error"] and res["moved"] == 0
    assert sorted(int(e["uid"]) for e in audit(world)) == sorted(world.uid(n) for n in JUNK_INBOX)
    for name in JUNK_INBOX:
        assert boxes(world, name) == ([(w.ADA, "INBOX")], [(w.ADA, "INBOX")]), name


FAR = 4_000_000_000                                              # a "now" long after every corpus date


def test_hwf_expiry_audits_before_it_moves(world):
    from mailmind.bootstrap.curation import rules
    rules.set_expiry("loom.example.test", 1)
    world.server.drop("UID MOVE", user=w.ADA)
    res = curation.expiry.account(acct(), now=FAR)
    assert res["error"] and res["moved"] == 0
    assert [(e["uid"], e["bucket"]) for e in audit(world)] == [(str(world.uid("ada-inbox-03-newsletter.eml")),
                                                                "expired")]
    assert boxes(world, "ada-inbox-03-newsletter.eml")[0] == [(w.ADA, "INBOX")]


def test_hwf_expiry_prunes_the_local_row_by_message_id(world):
    from mailmind.bootstrap.curation import rules
    rules.set_expiry("thimble.example.test", 1)                 # the receipt's sender
    gone(world, "ada-inbox-01-lunch.eml")
    world.server.renumber(w.ADA, "INBOX")                       # the receipt is now UID 4 on the server
    assert curation.expiry.account(acct(), now=FAR)["moved"] == 1
    assert boxes(world, RECEIPT) == ([(w.ADA, "Trash")], [])
    assert world.where("ada-inbox-04-ci.eml")["db"][0]["uid"] == 4    # the row stored at UID 4 stays


def test_hwf_a_sweep_still_moves_the_junk_when_the_server_refuses_move(world):
    world.server.fail("UID MOVE", "BAD [CANNOT] MOVE not supported", times=None, user=w.ADA)
    assert curation.sweeps.sweep(acct())["moved"] == 3


def test_hwf_a_sweep_and_expiry_on_a_server_without_move_copy_then_expunge_one_uid(world):
    from mailmind.bootstrap.curation import rules
    world.server.without = {"MOVE"}
    since = world.mark()
    assert curation.sweeps.sweep(acct())["moved"] == 3
    rules.set_expiry("thimble.example.test", 1)                 # the receipt's sender
    assert curation.expiry.account(acct(), now=FAR)["moved"] == 1
    assert boxes(world, RECEIPT)[0] == [(w.ADA, "Trash")]
    kinds = [line.split(" ", 3)[2] + " " + line.split(" ", 3)[3].split(" ", 1)[0]
             for line in world.writes(since)]
    assert set(kinds) == {"UID COPY", "UID STORE", "UID EXPUNGE"}   # never MOVE, never a bulk EXPUNGE


def test_hwf_expiry_ages_mail_by_when_it_arrived_not_its_date_header(world):
    import email.utils
    from datetime import datetime, timezone
    from mailmind.bootstrap.curation import rules
    rules.set_expiry("loom.example.test", 72)
    now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    hour, month = now - timedelta(hours=1), now - timedelta(days=30)

    def issue(n, dated, arrived):
        raw = w.variant("ada-inbox-03-newsletter.eml", Message_ID=f"<loom-{n}@example.test>",
                        Date=email.utils.format_datetime(dated))
        world.server.deliver(w.ADA, "INBOX", raw, when=arrived)
    issue("old-header", month, hour)                            # written long ago, arrived an hour ago: kept
    issue("new-header", hour, month)                            # says an hour old, arrived a month ago: expired
    curation.expiry.account(acct(), now=now.timestamp())
    assert world.server.find(w.ADA, "INBOX", "<loom-old-header@example.test>")
    assert world.server.find(w.ADA, "Trash", "<loom-new-header@example.test>")


def test_hwf_a_sweep_of_stored_junk_finds_its_targets_in_the_live_inbox_by_message_id(world):
    gone(world, "ada-inbox-01-lunch.eml")
    world.server.renumber(w.ADA, "INBOX")                       # every stored UID is now one off
    res = curation.sweeps.sweep_stored(acct())
    assert (res["moved"], res["stragglers"]) == (3, [])
    for name in JUNK_INBOX:
        assert boxes(world, name) == ([(w.ADA, "Trash")], []), name
    for name in (RECEIPT, "ada-inbox-06-outlook-invite.eml"):
        assert boxes(world, name)[0] == [(w.ADA, "INBOX")], name
