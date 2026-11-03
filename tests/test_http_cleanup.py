"""Cleaning up a sender (/api/cleanup/execute): the block rule and the INBOX sweep, which Undo
reverses, then the unsubscribe the newest message offers. That is an RFC 8058 one-click POST, a
mailto: sent through the fake SMTP server, or a landing page handed back for the browser. The
golden files are in tests/golden/http_cleanup/.

The one-click POST would leave the machine, so `_unsub_oneclick_post` is replaced here by a
recorder; tests/test_unsub_post.py covers the real one against a local HTTPS server. The method
is resolved from a stored body only, so each test opens a message first.

Two known bugs shape these tests. The sweep deletes the local rows the unsubscribe is resolved
from, so a sender whose mail is all in the INBOX gets no unsubscribe at all; and one-click is
recognised only by `List=One-Click`, not RFC 8058's `List-Unsubscribe=One-Click`. To reach the
unsubscribe paths, the tests put the issue that carries the header in the Archive, which the
sweep leaves alone, and spell one-click the way the app looks for it. Strict xfails at the end
state the fixes.
"""
import pytest

from fixtures import world as w

LOOM = "news@loom.example.test"
SPINDLE = "deals@spindle.example.test"
NEWSLETTER = "ada-inbox-03-newsletter.eml"


@pytest.fixture
def posts(monkeypatch):
    """The one-click POSTs the app made, answered as `posts.reply` says."""
    import server

    class Posts(list):
        reply = (True, 200, "ok")

    made = Posts()

    def post(url, _redirects=1):
        made.append(url)
        return made.reply

    monkeypatch.setattr(server, "_unsub_oneclick_post", post)
    return made


def pk_of(message_id):
    import store
    c = store.conn()
    try:
        return c.execute("SELECT pk FROM messages WHERE message_id=?", (message_id,)).fetchone()["pk"]
    finally:
        c.close()


def archived_issue(world, app, **headers):
    """A newer Loom issue, already filed in Ada's Archive, opened so its body is stored."""
    mid = "<issue-219@loom.example.test>"
    world.deliver(w.ADA, "Archive", w.variant(NEWSLETTER, Message_ID=mid, Date="Mon, 02 Nov 2026 07:00:00 +0000",
                                              Subject="Issue 219: the archived issue", **headers), (w.SEEN,))
    assert app.get(f"/api/message/{pk_of(mid)}/body").status == 200


ONE_CLICK = {"List_Unsubscribe_Post": "List=One-Click"}          # the spelling the app accepts today
MAILTO = {"List_Unsubscribe": "<mailto:unsubscribe@loom.example.test?subject=unsubscribe%20ada>"}


def preview(app, golden, addr):
    return golden.record(f"preview {addr}", app.get(f"/api/cleanup/sender?addr={addr}")).json


def execute(app, golden, label, body):
    r = golden.record(label, app.post("/api/cleanup/execute", body))
    if isinstance(r.json, dict) and r.json.get("undo_group"):
        golden.alias(r.json["undo_group"], "<undo group>")
    return r.json


def state(golden, world, label):
    import rules
    golden.note(f"{label}: block rules", {k: v for k, v in rules.load().items() if k.startswith("block")})
    golden.note(f"{label}: where the newsletter is", world.where(NEWSLETTER))


def test_a_cleanup_blocks_and_sweeps_and_undo_reverses_both(app, golden, world, posts):
    for n, day in ((216, "14"), (217, "21")):           # two older issues, so the sweep moves three
        world.deliver(w.ADA, "INBOX", w.variant(NEWSLETTER, Message_ID=f"<issue-{n}@loom.example.test>",
                                                Date=f"Wed, {day} Oct 2026 07:00:00 +0000",
                                                Subject=f"Issue {n}: weaving notes"))
    app.get(f"/api/message/{world.pk(NEWSLETTER)}/body")
    token = preview(app, golden, LOOM)["confirm_token"]
    since = world.mark()
    execute(app, golden, "clean up the Loom", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token})
    golden.note("one-click POSTs", list(posts))
    golden.note("SMTP messages", len(world.smtp.messages))
    golden.note("server writes", world.writes(since))
    state(golden, world, "after")
    golden.record("the audit trail", app.get("/api/audit?kind=all"))
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
    golden.note("on the wire", [{"login": d.user, "mail_from": d.mail_from, "rcpts": d.rcpts,
                                 "data": d.data.decode("utf-8", "replace")} for d in world.smtp.messages])
    golden.note("one-click POSTs", list(posts))


def test_a_landing_page_goes_back_to_the_browser(app, golden, world, posts):
    app.get(f"/api/message/{world.pk('ada-trash-01-promo.eml')}/body")
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


def test_block_the_domain_without_unsubscribing(app, golden, world, posts):
    execute(app, golden, "block loom.example.test", {"from_addr": "News@Loom.example.test", "block_scope": "domain"})
    golden.note("one-click POSTs", list(posts))
    state(golden, world, "after")


def test_cleanup_refusals(app, golden):
    for label, body in (("no address", {}), ("not an address", {"from_addr": "loom"})):
        execute(app, golden, label, body)


def rows(sql):
    import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql)]
    finally:
        c.close()


# ── known bugs ───────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="known bug: RFC 8058's List-Unsubscribe=One-Click isn't recognised")
def test_an_rfc_8058_sender_is_offered_one_click(app, world):
    app.get(f"/api/message/{world.pk(NEWSLETTER)}/body")
    assert app.get(f"/api/cleanup/sender?addr={LOOM}").json["unsub"]["method"] == "one-click"


@pytest.mark.xfail(strict=True, reason="known bug: the sweep deletes the rows the unsubscribe is resolved from")
def test_a_sender_whose_mail_is_in_the_inbox_is_still_unsubscribed(app, world, posts):
    world.deliver(w.ADA, "INBOX", w.variant(NEWSLETTER, Message_ID="<issue-219@loom.example.test>",
                                            Date="Mon, 02 Nov 2026 07:00:00 +0000", **MAILTO))
    app.get(f"/api/message/{pk_of('<issue-219@loom.example.test>')}/body")
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token}).json
    assert (r["swept"], r["status"]) == (2, "mailto_sent")


@pytest.mark.xfail(strict=True, reason="known bug: the mailto unsubscribe passes a dict as the envelope")
def test_a_mailto_unsubscribe_reaches_the_list(app, world, posts):
    archived_issue(world, app, **MAILTO)
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token})
    assert [d.rcpts for d in world.smtp.messages] == [["unsubscribe@loom.example.test"]]


@pytest.mark.xfail(strict=True, reason="known bug: the mailto unsubscribe is sent with bare LF line ends")
def test_a_mailto_unsubscribe_ends_its_lines_with_crlf(app, world, posts):
    archived_issue(world, app, **MAILTO)
    token = app.get(f"/api/cleanup/sender?addr={LOOM}").json["confirm_token"]
    app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": token})
    [d] = world.smtp.messages
    assert b"\n" not in d.data.replace(b"\r\n", b"")
