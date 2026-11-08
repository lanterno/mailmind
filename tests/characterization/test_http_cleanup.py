"""Cleaning up a sender (/api/cleanup/execute): the block rule and the INBOX sweep, which Undo
reverses, then the unsubscribe the newest message offers. That is an RFC 8058 one-click POST, a
mailto: sent through the fake SMTP server, or a landing page handed back for the browser. The
golden files are in tests/golden/http_cleanup/.

A mailto: goes through the outbox, as anything sent does, so what it puts on the wire comes after Undo Send's window.

The one-click POST would leave the machine, so `safe_http.one_click_post` is replaced here by a
recorder; tests/unit/curation/test_unsub_post.py covers the real one against a local HTTPS server. The method
is resolved from a stored body only, so each test opens a message first. It is resolved before
the sweep, which deletes the rows it moves; most tests put the issue that carries the header in
the Archive, which the sweep leaves alone, and the ones at the end keep it in the INBOX.
"""
from datetime import timedelta
from urllib.parse import urlencode

import pytest

from fixtures import world as w
from mailmind.bootstrap import accounts
from mailmind.bootstrap.curation import safe_http
from mailmind.bootstrap import mailbox

LOOM = "news@loom.example.test"
SPINDLE = "deals@spindle.example.test"
NEWSLETTER = "ada-inbox-03-newsletter.eml"


@pytest.fixture
def posts(monkeypatch):
    """The one-click POSTs the app made, answered as `posts.reply` says."""

    class Posts(list):
        reply = (True, 200, "ok")

    made = Posts()

    def post(url, _redirects=1):
        made.append(url)
        return made.reply

    monkeypatch.setattr(safe_http, "one_click_post", post)
    return made


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
    """Undo Send's window passes, and what's queued goes."""
    from mailmind.bootstrap.compose import outbox
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()


def archived_issue(world, app, **headers):
    """A newer Loom issue, already filed in Ada's Archive, opened so its body is stored."""
    mid = "<issue-219@loom.example.test>"
    world.deliver(w.ADA, "Archive", w.variant(NEWSLETTER, Message_ID=mid, Date="Mon, 02 Nov 2026 07:00:00 +0000",
                                              Subject="Issue 219: the archived issue", **headers), (w.SEEN,))
    assert opened(app, w.copy_of(pk_of(mid))).status == 200


ONE_CLICK = {"List_Unsubscribe_Post": "List-Unsubscribe=One-Click"}    # RFC 8058's, the one spelling
MAILTO = {"List_Unsubscribe": "<mailto:unsubscribe@loom.example.test?subject=unsubscribe%20ada>"}


def preview(app, golden, addr):
    return golden.record(f"preview {addr}", app.get(f"/api/cleanup/sender?addr={addr}")).json


def execute(app, golden, label, body):
    r = golden.record(label, app.post("/api/cleanup/execute", body))
    if isinstance(r.json, dict) and r.json.get("undo_group"):
        golden.alias(r.json["undo_group"], "<undo group>")
    return r.json


def state(golden, world, label):
    from mailmind.bootstrap.curation import rules
    golden.note(f"{label}: block rules", {k: v for k, v in rules.load().items() if k.startswith("block")})
    golden.note(f"{label}: where the newsletter is", world.where(NEWSLETTER))


def test_a_cleanup_blocks_and_sweeps_and_undo_reverses_both(app, golden, world, posts):
    for n, day in ((216, "14"), (217, "21")):           # two older issues, so the sweep moves three
        world.deliver(w.ADA, "INBOX", w.variant(NEWSLETTER, Message_ID=f"<issue-{n}@loom.example.test>",
                                                Date=f"Wed, {day} Oct 2026 07:00:00 +0000",
                                                Subject=f"Issue {n}: weaving notes"))
    opened(app, world.copy(NEWSLETTER))
    token = preview(app, golden, LOOM)["confirm_token"]
    since = world.mark()
    execute(app, golden, "clean up the Loom", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token})
    golden.note("one-click POSTs", list(posts))
    golden.note("SMTP messages", len(world.smtp.messages))
    golden.note("server writes", world.writes(since))
    state(golden, world, "after")
    golden.record("the audit trail", app.get("/api/audit"))
    since = world.mark()
    golden.record("undo", app.post("/api/undo", {}))
    golden.note("server writes for the undo", world.writes(since))
    state(golden, world, "after the undo")


def test_one_click_posts_once_and_a_failure_stays_retryable(app, golden, world, posts):
    archived_issue(world, app, **ONE_CLICK)
    token = preview(app, golden, LOOM)["confirm_token"]
    posts.reply = (False, 503, "bad_status")
    execute(app, golden, "the list's server is down", {"from_addr": LOOM, "do_unsubscribe": True,
                                                       "confirm_token": token})
    posts.reply = (True, 200, "ok")
    execute(app, golden, "try again", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token})
    execute(app, golden, "and again, once it worked", {"from_addr": LOOM, "do_unsubscribe": True,
                                                       "confirm_token": token})
    golden.note("one-click POSTs", list(posts))
    golden.note("the unsubscribe ledger", rows("SELECT * FROM unsub_log"))


def test_a_mailto_unsubscribe_goes_out_by_smtp(app, golden, world, posts):
    archived_issue(world, app, **MAILTO)
    token = preview(app, golden, LOOM)["confirm_token"]
    execute(app, golden, "clean up the Loom by mail", {"from_addr": LOOM, "do_unsubscribe": True,
                                                       "confirm_token": token})
    out(app)
    import email
    for d in world.smtp.messages:
        golden.alias(email.message_from_bytes(d.data)["Message-ID"], "<message id>")
    golden.note("on the wire", [{"login": d.user, "mail_from": d.mail_from, "rcpts": d.rcpts,
                                 "data": d.data.decode("utf-8", "replace")} for d in world.smtp.messages])
    golden.note("one-click POSTs", list(posts))


def test_a_landing_page_goes_back_to_the_browser(app, golden, world, posts):
    opened(app, world.copy("ada-trash-01-promo.eml"))
    token = preview(app, golden, SPINDLE)["confirm_token"]
    execute(app, golden, "clean up Spindle", {"from_addr": SPINDLE, "do_unsubscribe": True, "confirm_token": token})
    execute(app, golden, "again", {"from_addr": SPINDLE, "do_unsubscribe": True, "confirm_token": token})
    golden.note("one-click POSTs", list(posts))


def test_without_the_preview_token_nothing_leaves(app, golden, world, posts):
    archived_issue(world, app, **ONE_CLICK)
    execute(app, golden, "no token", {"from_addr": LOOM, "do_unsubscribe": True})
    execute(app, golden, "another sender's token", {"from_addr": LOOM, "do_unsubscribe": True,
                                                     "confirm_token": preview(app, golden, SPINDLE)["confirm_token"]})
    golden.note("one-click POSTs", list(posts))
    state(golden, world, "after")


def test_a_preview_stands_for_ten_minutes(app, world, posts):
    archived_issue(world, app, **ONE_CLICK)
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    app.clock.shift(timedelta(minutes=10))
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token}).json
    assert (r["status"], len(posts)) == ("unsubscribed_oneclick", 1)


def test_an_older_preview_sends_nothing_and_the_block_and_sweep_stand(app, world, posts):
    archived_issue(world, app, **ONE_CLICK)
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    app.clock.shift(timedelta(minutes=10, seconds=1))
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token}).json
    assert (r["ok"], r["status"], posts) == (True, "no_token", [])
    assert r["blocked_token"] == LOOM and r["swept"]


def test_a_list_that_changed_its_way_out_since_the_preview_needs_a_new_look(app, world, posts):
    archived_issue(world, app, **ONE_CLICK)
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    mid = "<issue-220@loom.example.test>"
    world.deliver(w.ADA, "Archive", w.variant(NEWSLETTER, Message_ID=mid, Date="Mon, 02 Nov 2026 11:00:00 +0000",
                                              Subject="Issue 220", **MAILTO), (w.SEEN,))
    assert opened(app, w.copy_of(pk_of(mid))).status == 200
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token}).json
    assert (r["status"], posts, world.smtp.messages) == ("no_token", [], [])


@pytest.mark.parametrize("token", ["1800000000", "not.a-token", "\u0663.abc"])
def test_what_isnt_a_token_sends_nothing(app, world, posts, token):
    archived_issue(world, app, **ONE_CLICK)
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token}).json
    assert (r["status"], posts) == ("no_token", [])


def test_block_the_domain_without_unsubscribing(app, golden, world, posts):
    execute(app, golden, "block loom.example.test", {"from_addr": "News@Loom.example.test", "block_scope": "domain"})
    golden.note("one-click POSTs", list(posts))
    state(golden, world, "after")


def test_cleanup_refusals(app, golden):
    for label, body in (("no address", {}), ("not an address", {"from_addr": "loom"})):
        execute(app, golden, label, body)


def rows(sql):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql)]
    finally:
        c.close()


# ── the unsubscribe goes out ─────────────────────────────────────────────

def test_an_rfc_8058_sender_is_offered_one_click(app, world):
    opened(app, world.copy(NEWSLETTER))
    assert app.get(f"/api/cleanup/sender?addr={LOOM}").json["unsub"]["method"] == "one-click"


def test_a_sender_whose_mail_is_in_the_inbox_is_still_unsubscribed(app, world, posts):
    world.deliver(w.ADA, "INBOX", w.variant(NEWSLETTER, Message_ID="<issue-219@loom.example.test>",
                                            Date="Mon, 02 Nov 2026 07:00:00 +0000", **MAILTO))
    opened(app, w.copy_of(pk_of("<issue-219@loom.example.test>")))
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token}).json
    assert (r["swept"], r["status"]) == (2, "mailto_sent")


def test_a_mailto_unsubscribe_reaches_the_list(app, world, posts):
    archived_issue(world, app, **MAILTO)
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token})
    assert world.smtp.messages == []                           # queued, like anything sent
    out(app)
    assert [d.rcpts for d in world.smtp.messages] == [["unsubscribe@loom.example.test"]]


def test_a_mailto_unsubscribe_shows_in_the_outbox_and_is_filed_in_sent(app, world, posts):
    archived_issue(world, app, **MAILTO)
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token})
    [item] = app.get("/api/outbox").json["items"]
    assert (item["subject"], item["to"], item["state"]) == ("unsubscribe ada", ["unsubscribe@loom.example.test"], "queued")
    out(app)
    [item] = app.get("/api/outbox").json["items"]
    assert item["state"] == "sent"
    [d] = world.smtp.messages
    import email
    mid = email.message_from_bytes(d.data)["Message-ID"]
    from mailmind.bootstrap.compose import outbox
    assert outbox._copies.in_sent(accounts.get(w.ADA), mid)


def test_a_mailto_unsubscribe_waits_out_a_server_thats_away(app, world, posts):
    archived_issue(world, app, **MAILTO)
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token}).json
    assert r["status"] == "mailto_sent"
    world.smtp.fail("MAIL")
    out(app)
    assert world.smtp.messages == []
    from mailmind.bootstrap.compose import outbox
    app.clock.shift(timedelta(seconds=outbox._BACKOFF_CAP + 1))
    outbox.drain()
    assert len(world.smtp.messages) == 1


def test_a_mailto_unsubscribe_from_a_removed_account_is_skipped_not_a_failure_of_the_cleanup(app, world, posts, monkeypatch):
    archived_issue(world, app, **MAILTO)
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]

    def gone(_):
        raise KeyError("no such account")
    monkeypatch.setattr(accounts, "get", gone)
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token}).json
    assert (r["ok"], r["status"]) == (True, "failed")


def test_a_mailto_unsubscribe_ends_its_lines_with_crlf(app, world, posts):
    archived_issue(world, app, **MAILTO)
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token})
    out(app)
    [d] = world.smtp.messages
    assert b"\n" not in d.data.replace(b"\r\n", b"")


def test_one_click_spelled_any_other_way_is_never_posted(app, world, posts):
    archived_issue(world, app, List_Unsubscribe_Post="List=One-Click")      # the issue offers mailto too
    assert app.get(f"/api/cleanup/sender?addr={LOOM}").json["unsub"]["method"] == "mailto"
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token}).json
    assert (r["status"], posts) == ("mailto_sent", [])


def test_a_mailto_unsubscribe_is_a_whole_message_from_the_account(app, world, posts):
    archived_issue(world, app, **MAILTO)
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token})
    out(app)
    [d] = world.smtp.messages
    import email
    m = email.message_from_bytes(d.data)
    assert (d.mail_from, m["From"], m["To"], m["Subject"]) == (w.ADA, w.ADA, "unsubscribe@loom.example.test",
                                                                "unsubscribe ada")
    assert m["Date"] and m["Message-ID"].endswith(f"@{w.ADA.rpartition('@')[2]}>")


@pytest.mark.parametrize("target", ["unsubscribe@loom.example.test,ada@example.test",
                                    "unsubscribe@loom.example.test%0d%0aBcc:ada@example.test",
                                    "not-an-address"])
def test_a_mailto_unsubscribe_goes_to_one_plain_address_or_nowhere(app, world, posts, target):
    archived_issue(world, app, List_Unsubscribe=f"<mailto:{target}>")
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token}).json
    assert (r["status"], world.smtp.messages) == ("failed", [])


def test_a_cleanup_refreshes_the_folder_counts_as_trashing_a_message_does(app, world, monkeypatch):
    refreshed = []
    real = mailbox.folders.invalidate
    monkeypatch.setattr(mailbox.folders, "invalidate", lambda *a: (refreshed.append(a), real(*a)))
    r = _clean_up_the_loom(app)
    assert r["swept"] > 0 and len(refreshed) >= r["swept"]


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
