"""The cleanups of tests/api/test_http_cleanup.py whose one-click unsubscribe POST must be made and answered, run against
the app in this process. The golden files are in tests/golden/http_cleanup_in_process/.

A served app can't make that POST here: it is https only, refuses any host that resolves to loopback, and its lookup of
the list's host is one the tripwire refuses. So `safe_http.one_click_post` is replaced by a recorder, which only reaches
the app in this process; tests/unit/curation/test_unsub_post.py covers the real one against a local HTTPS server.
The method is resolved from a stored body only, so each test opens a message first.
"""
from urllib.parse import urlencode

import pytest

from fixtures import world as w
from mailmind.bootstrap.curation import safe_http

LOOM = "news@loom.example.test"
NEWSLETTER = "ada-inbox-03-newsletter.eml"
ONE_CLICK = {"List_Unsubscribe_Post": "List-Unsubscribe=One-Click"}    # RFC 8058's, the one spelling


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


def rows(sql, *args):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def opened(app, copy):
    """Open a copy of a message, so its body is stored."""
    return app.get("/api/message/body?" + urlencode(copy))


def archived_issue(world, app, **headers):
    """A newer Loom issue, already filed in Ada's Archive, which the sweep leaves alone, opened so its body is stored."""
    mid = "<issue-219@loom.example.test>"
    world.deliver(w.ADA, "Archive", w.variant(NEWSLETTER, Message_ID=mid, Date="Mon, 02 Nov 2026 07:00:00 +0000",
                                              Subject="Issue 219: the archived issue", **headers), (w.SEEN,))
    [found] = rows("SELECT pk FROM messages WHERE message_id=?", mid)
    assert opened(app, w.copy_of(found["pk"])).status == 200


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
