"""Cleaning up a sender (/api/cleanup/execute): the block rule and the INBOX sweep, which Undo
reverses, then the unsubscribe the newest message offers. That is an RFC 8058 one-click POST, a
mailto: sent through the fake SMTP server, or a landing page handed back for the browser. The
golden files are in tests/golden/http_cleanup/.

A mailto: goes through the outbox, as anything sent does, so what it puts on the wire comes after Undo Send's window,
when the server runs an outbox pass (app.work).

The one-click POST can't be made by a served app here: it is https only, refuses a host that resolves to loopback, and
its lookup of the list's host is one the server's tripwire refuses and reports, which fails the test. So these tests
see that none was made, and the cleanups whose POST must be made and answered are in
tests/characterization/test_http_cleanup_in_process.py. The method is resolved from a stored body only, so each test
opens a message first. It is resolved before the sweep, which deletes the rows it moves; most tests put the issue that
carries the header in the Archive, which the sweep leaves alone, and the ones at the end keep it in the INBOX.
"""
import email
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest

from fixtures import world as w

LOOM = "news@loom.example.test"
SPINDLE = "deals@spindle.example.test"
NEWSLETTER = "ada-inbox-03-newsletter.eml"


def one_click_posts():
    """The one-click POSTs the server made: none, in a test that passes. A POST looks up the list's host first, which the
    server's tripwire refuses and reports, and the `app` fixture fails the test on what it reported."""
    return []


def pk_of(message_id):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return c.execute("SELECT pk FROM messages WHERE message_id=?", (message_id,)).fetchone()["pk"]
    finally:
        c.close()


def opened(app, copy):
    """Open a copy of a message, so its body is stored."""
    return app.get("/api/message/body?" + urlencode(copy))


def out(app):
    """The time the outbox says its queued mail goes comes (the end of Undo Send's window, or the next try after a
    failed one), and the server runs an outbox pass."""
    due = max(i["next_attempt_at"] for i in app.get("/api/outbox").json["items"] if i["state"] == "queued")
    app.clock.move_to(max(app.clock.now, datetime.fromtimestamp(due, timezone.utc)) + timedelta(seconds=1))
    app.work("outbox")


def archived_issue(world, app, **headers):
    """A newer Loom issue, already filed in Ada's Archive, opened so its body is stored."""
    mid = "<issue-219@loom.example.test>"
    world.deliver(w.ADA, "Archive", w.variant(NEWSLETTER, Message_ID=mid, Date="Mon, 02 Nov 2026 07:00:00 +0000",
                                              Subject="Issue 219: the archived issue", **headers), (w.SEEN,))
    assert opened(app, w.copy_of(pk_of(mid))).status == 200


ONE_CLICK = {"List_Unsubscribe_Post": "List-Unsubscribe=One-Click"}    # RFC 8058's, the one spelling
MAILTO = {"List_Unsubscribe": "<mailto:unsubscribe@loom.example.test?subject=unsubscribe%20ada>"}


def preview(app, golden, addr):
    answer = golden.record(f"preview {addr}", app.get(f"/api/cleanup/sender?addr={addr}")).json
    # Keyed with a secret the server draws as it starts, so it is a different token from each one.
    golden.alias(answer["confirm_token"], "<confirm token>")
    return answer


def execute(app, golden, label, body):
    r = golden.record(label, app.post("/api/cleanup/execute", body))
    if isinstance(r.json, dict) and r.json.get("undo_group"):
        golden.alias(r.json["undo_group"], "<undo group>")
    return r.json


def state(app, golden, world, label):
    rules = app.get("/api/rules").json["rules"]
    golden.note(f"{label}: block rules", {k: v for k, v in rules.items() if k.startswith("block")})
    golden.note(f"{label}: where the newsletter is", world.where(NEWSLETTER))


def test_a_mailto_unsubscribe_goes_out_by_smtp(app, golden, world):
    archived_issue(world, app, **MAILTO)
    token = preview(app, golden, LOOM)["confirm_token"]
    execute(app, golden, "clean up the Loom by mail", {"from_addr": LOOM, "do_unsubscribe": True,
                                                       "confirm_token": token})
    out(app)
    for d in world.smtp.messages:
        golden.alias(email.message_from_bytes(d.data)["Message-ID"], "<message id>")
    golden.note("on the wire", [{"login": d.user, "mail_from": d.mail_from, "rcpts": d.rcpts,
                                 "data": d.data.decode("utf-8", "replace")} for d in world.smtp.messages])
    golden.note("one-click POSTs", one_click_posts())


def test_a_landing_page_goes_back_to_the_browser(app, golden, world):
    opened(app, world.copy("ada-trash-01-promo.eml"))
    token = preview(app, golden, SPINDLE)["confirm_token"]
    execute(app, golden, "clean up Spindle", {"from_addr": SPINDLE, "do_unsubscribe": True, "confirm_token": token})
    execute(app, golden, "again", {"from_addr": SPINDLE, "do_unsubscribe": True, "confirm_token": token})
    golden.note("one-click POSTs", one_click_posts())


def test_without_the_preview_token_nothing_leaves(app, golden, world):
    archived_issue(world, app, **ONE_CLICK)
    execute(app, golden, "no token", {"from_addr": LOOM, "do_unsubscribe": True})
    execute(app, golden, "another sender's token", {"from_addr": LOOM, "do_unsubscribe": True,
                                                     "confirm_token": preview(app, golden, SPINDLE)["confirm_token"]})
    golden.note("one-click POSTs", one_click_posts())
    state(app, golden, world, "after")


def test_a_preview_stands_for_ten_minutes(app, world):
    # By mail, as the one way out a served app can take here, so that what it sent can be seen.
    archived_issue(world, app, **MAILTO)
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    app.clock.shift(timedelta(minutes=10))
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token}).json
    assert r["status"] == "mailto_sent"
    assert [i["to"] for i in app.get("/api/outbox").json["items"]] == [["unsubscribe@loom.example.test"]]


def test_an_older_preview_sends_nothing_and_the_block_and_sweep_stand(app, world):
    archived_issue(world, app, **ONE_CLICK)
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    app.clock.shift(timedelta(minutes=10, seconds=1))
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token}).json
    assert (r["ok"], r["status"], one_click_posts()) == (True, "no_token", [])
    assert r["blocked_token"] == LOOM and r["swept"]


def test_a_list_that_changed_its_way_out_since_the_preview_needs_a_new_look(app, world):
    archived_issue(world, app, **ONE_CLICK)
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    mid = "<issue-220@loom.example.test>"
    world.deliver(w.ADA, "Archive", w.variant(NEWSLETTER, Message_ID=mid, Date="Mon, 02 Nov 2026 11:00:00 +0000",
                                              Subject="Issue 220", **MAILTO), (w.SEEN,))
    assert opened(app, w.copy_of(pk_of(mid))).status == 200
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token}).json
    assert (r["status"], one_click_posts(), world.smtp.messages) == ("no_token", [], [])


@pytest.mark.parametrize("token", ["1800000000", "not.a-token", "\u0663.abc"])
def test_what_isnt_a_token_sends_nothing(app, world, token):
    archived_issue(world, app, **ONE_CLICK)
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token}).json
    assert (r["status"], one_click_posts()) == ("no_token", [])


def test_block_the_domain_without_unsubscribing(app, golden, world):
    execute(app, golden, "block loom.example.test", {"from_addr": "News@Loom.example.test", "block_scope": "domain"})
    golden.note("one-click POSTs", one_click_posts())
    state(app, golden, world, "after")


def test_cleanup_refusals(app, golden):
    for label, body in (("no address", {}), ("not an address", {"from_addr": "loom"})):
        execute(app, golden, label, body)


# ── the unsubscribe goes out ─────────────────────────────────────────────

def test_an_rfc_8058_sender_is_offered_one_click(app, world):
    opened(app, world.copy(NEWSLETTER))
    assert app.get(f"/api/cleanup/sender?addr={LOOM}").json["unsub"]["method"] == "one-click"


def test_a_sender_whose_mail_is_in_the_inbox_is_still_unsubscribed(app, world):
    world.deliver(w.ADA, "INBOX", w.variant(NEWSLETTER, Message_ID="<issue-219@loom.example.test>",
                                            Date="Mon, 02 Nov 2026 07:00:00 +0000", **MAILTO))
    opened(app, w.copy_of(pk_of("<issue-219@loom.example.test>")))
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token}).json
    assert (r["swept"], r["status"]) == (2, "mailto_sent")


def test_a_mailto_unsubscribe_reaches_the_list(app, world):
    archived_issue(world, app, **MAILTO)
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token})
    assert world.smtp.messages == []                           # queued, like anything sent
    out(app)
    assert [d.rcpts for d in world.smtp.messages] == [["unsubscribe@loom.example.test"]]


def test_a_mailto_unsubscribe_shows_in_the_outbox_and_is_filed_in_sent(app, world):
    archived_issue(world, app, **MAILTO)
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token})
    [item] = app.get("/api/outbox").json["items"]
    assert (item["subject"], item["to"], item["state"]) == ("unsubscribe ada", ["unsubscribe@loom.example.test"], "queued")
    out(app)
    [item] = app.get("/api/outbox").json["items"]
    assert item["state"] == "sent"
    [d] = world.smtp.messages
    mid = email.message_from_bytes(d.data)["Message-ID"]
    assert [(user, box) for user, box, _uid, _flags in world.where_id(mid)] == [(w.ADA, "Sent")]


def test_a_mailto_unsubscribe_waits_out_a_server_thats_away(app, world):
    archived_issue(world, app, **MAILTO)
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token}).json
    assert r["status"] == "mailto_sent"
    world.smtp.fail("MAIL")
    out(app)
    assert world.smtp.messages == []
    out(app)                                                    # its next try, which the outbox put off
    assert len(world.smtp.messages) == 1


def test_a_mailto_unsubscribe_from_a_removed_account_is_skipped_not_a_failure_of_the_cleanup(app, world):
    archived_issue(world, app, **MAILTO)
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    assert app.post("/api/accounts/delete", {"email": w.ADA}).status == 200    # removed between the preview and the yes
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token}).json
    assert (r["ok"], r["status"]) == (True, "failed")


def test_a_mailto_unsubscribe_ends_its_lines_with_crlf(app, world):
    archived_issue(world, app, **MAILTO)
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token})
    out(app)
    [d] = world.smtp.messages
    assert b"\n" not in d.data.replace(b"\r\n", b"")


def test_one_click_spelled_any_other_way_is_never_posted(app, world):
    archived_issue(world, app, List_Unsubscribe_Post="List=One-Click")      # the issue offers mailto too
    assert app.get(f"/api/cleanup/sender?addr={LOOM}").json["unsub"]["method"] == "mailto"
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token}).json
    assert (r["status"], one_click_posts()) == ("mailto_sent", [])


def test_a_mailto_unsubscribe_is_a_whole_message_from_the_account(app, world):
    archived_issue(world, app, **MAILTO)
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token})
    out(app)
    [d] = world.smtp.messages
    m = email.message_from_bytes(d.data)
    assert (d.mail_from, m["From"], m["To"], m["Subject"]) == (w.ADA, w.ADA, "unsubscribe@loom.example.test",
                                                                "unsubscribe ada")
    assert m["Date"] and m["Message-ID"].endswith(f"@{w.ADA.rpartition('@')[2]}>")


@pytest.mark.parametrize("target", ["unsubscribe@loom.example.test,ada@example.test",
                                    "unsubscribe@loom.example.test%0d%0aBcc:ada@example.test",
                                    "not-an-address"])
def test_a_mailto_unsubscribe_goes_to_one_plain_address_or_nowhere(app, world, target):
    archived_issue(world, app, List_Unsubscribe=f"<mailto:{target}>")
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token}).json
    assert (r["status"], world.smtp.messages) == ("failed", [])


def test_a_cleanup_refreshes_the_folder_counts_as_trashing_a_message_does(app, world):
    def counts():
        return {f["name"]: f["total"] for f in app.get(f"/api/folders?account={w.ADA}").json["folders"]}
    # The rail's counts, which the server keeps a while: the clock stands still, so only a refresh changes them.
    before = counts()
    r = _clean_up_the_loom(app)
    after = counts()
    assert r["swept"] > 0
    assert (after["INBOX"], after["Trash"]) == (before["INBOX"] - r["swept"], before["Trash"] + r["swept"])


def _clean_up_the_loom(app):
    return app.post("/api/cleanup/execute", {"from_addr": LOOM,
                                             "confirm_token": app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]}).json


def test_a_cleanup_with_the_server_away_queues_the_sweep_and_undo_takes_it_back(app, world):
    from mailmind.bootstrap.mailbox import store
    world.server.stop()
    r = _clean_up_the_loom(app)
    assert (r["swept"], r["sweep_queued"], r["sweep_failed"]) == (1, 1, 0)
    c = store.conn()
    assert c.execute("SELECT count(*) FROM mail_commands WHERE kind='trash' AND applied=0").fetchone()[0] == 1
    assert c.execute("SELECT pending_removal IS NOT NULL FROM messages WHERE pk=?", (world.pk(NEWSLETTER),)).fetchone()[0]
    c.close()
    undone = app.post("/api/undo", {}).json
    assert undone["ok"]
    c = store.conn()
    assert c.execute("SELECT pending_removal FROM messages WHERE pk=?", (world.pk(NEWSLETTER),)).fetchone()[0] is None
    c.close()


def test_a_cleanup_the_server_refuses_counts_as_failed_and_leaves_the_mail(app, world):
    world.server.fail("UID MOVE", "NO [CANNOT] not today", user=w.ADA)
    world.server.fail("UID COPY", "NO [CANNOT] not today", user=w.ADA)
    r = _clean_up_the_loom(app)
    assert (r["swept"], r["sweep_queued"], r["sweep_failed"]) == (0, 0, 1)
    assert ["INBOX"] == [box for _user, box, _uid, _flags in world.where(NEWSLETTER)["server"]]
