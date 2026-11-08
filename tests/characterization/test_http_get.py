"""Every GET route, answered over the fictional world with the clock frozen at conftest.NOW.

Each test records what the app answered (status, the headers a browser acts on, the JSON body)
into its golden file under tests/golden/http_get/. A change in any answer fails the test until
the file is rewritten with `uv run pytest --update-golden` and the diff reviewed: that diff is the
behaviour change.

AI is off here: there is no key, so nothing reaches the fake Claude API. The AI routes with a key
are in test_http_ai.py.
"""
import sqlite3
from urllib.parse import quote, urlencode

import pytest

import problems
from fixtures import world as w
from mailmind.bootstrap.assistant import answers


def fetch(app, golden, path, label=None, **kw):
    return golden.record(label or "GET " + path, app.get(path, **kw))


def body_of(copy, **more):
    """Where the reader opens a copy, by its key and folder."""
    return "/api/message/body?" + urlencode({**copy, **more})


def attachment_of(copy, part_id):
    return "/api/message/attachment?" + urlencode({**copy, "part_id": part_id})


# ── the page and the guards every GET passes ─────────────────────────────────

def test_the_page_carries_this_processes_csrf_token(app, golden):
    import app_client
    r = app.get("/")
    golden.record("GET /", r, body=False)
    page = r.body.decode("utf-8")
    assert "__CSRF_TOKEN__" not in page
    assert f'content="{app_client.CSRF}"' in page


def test_guards_refuse_foreign_hosts_and_cross_site_reads(app, golden):
    fetch(app, golden, "/api/accounts", "GET /api/accounts from a rebinding host",
          headers={"Host": "evil.example.test"})
    fetch(app, golden, "/api/accounts", "GET /api/accounts with a foreign Origin",
          headers={"Origin": "http://evil.example.test"})
    fetch(app, golden, "/api/accounts", "GET /api/accounts marked cross-site",
          headers={"Sec-Fetch-Site": "cross-site"})
    fetch(app, golden, "/api/accounts", "GET /api/accounts marked same-site",
          headers={"Sec-Fetch-Site": "same-site"})
    golden.record("GET / as a top-level cross-site visit",       # served; its content isn't this test's
                  app.get("/", headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
                                        "Sec-Fetch-Dest": "document"}), body=False)
    fetch(app, golden, "/api/accounts", "GET /api/accounts without Sec-Fetch-Site",
          headers={"Sec-Fetch-Site": None})
    fetch(app, golden, "/api/accounts", "GET /api/accounts via localhost",
          headers={"Host": f"localhost:{app.port}"})
    fetch(app, golden, "/api/no-such-route")
    fetch(app, golden, "/api/messages?account=ada@example.test&limit=lots")     # a bad int: 400


# ── accounts, settings and the stores beside the database ───────────────────

def test_accounts_rules_views_and_audit(app, golden):
    fetch(app, golden, "/api/accounts")
    fetch(app, golden, "/api/rules")
    fetch(app, golden, "/api/views")
    fetch(app, golden, "/api/audit")
    fetch(app, golden, "/api/audit?limit=5")
    fetch(app, golden, "/api/outbox")
    fetch(app, golden, "/api/debug/pool")


def test_account_health_and_stats(app, golden):
    fetch(app, golden, "/api/account-health")
    fetch(app, golden, "/api/stats")


def test_export_plan_and_database_export(app, golden, tmp_path):
    fetch(app, golden, "/api/export/plan")
    r = app.get("/api/export")
    golden.record("GET /api/export", r, body=False)
    assert r.body.startswith(b"SQLite format 3\x00")
    (tmp_path / "export.db").write_bytes(r.body)
    db = sqlite3.connect(tmp_path / "export.db")
    try:
        golden.note("messages in the export", db.execute("SELECT count(*) FROM messages").fetchone()[0])
    finally:
        db.close()


# ── the inbox: digest, folders, lists, threads ─────────────────────────────

def test_digest(app, golden):
    fetch(app, golden, "/api/digest")


def test_folders(app, golden):
    fetch(app, golden, "/api/folders")
    for acct in (w.ADA, w.BEA, w.CY):
        fetch(app, golden, f"/api/folders?account={acct}")


def test_message_lists(app, golden):
    fetch(app, golden, f"/api/messages?account={w.ADA}")
    fetch(app, golden, f"/api/messages?account={w.ADA}&group=thread")
    fetch(app, golden, f"/api/messages?account={w.ADA}&offset=2&limit=3")
    fetch(app, golden, f"/api/messages?account={w.ADA}&folder=Drafts")
    fetch(app, golden, f"/api/messages?account={w.ADA}&folder=Trash")
    fetch(app, golden, f"/api/messages?account={w.BEA}&folder=Folders/Clients")
    fetch(app, golden, f"/api/messages?account={w.CY}&group=thread")
    fetch(app, golden, f"/api/messages?account={w.CY}&folder=[Gmail]/Sent Mail".replace(" ", "%20"))
    fetch(app, golden, f"/api/messages?account={w.ADA}&folder=Nowhere")


def test_threads(app, golden, world):
    for name in ("ada-inbox-01-lunch.eml", "cy-inbox-01-thread-start.eml", "bea-inbox-01-client.eml"):
        row = world.row(name)
        fetch(app, golden, f"/api/thread?account={row['account']}&thread_id={row['thread_id']}",
              f"GET /api/thread for {name}")
    fetch(app, golden, f"/api/thread?account={w.ADA}&thread_id=no-such-thread")


def test_upcoming_screener_and_contacts(app, golden):
    fetch(app, golden, "/api/upcoming")
    fetch(app, golden, "/api/screener")
    fetch(app, golden, f"/api/screener?account={w.ADA}")
    fetch(app, golden, "/api/contacts")
    fetch(app, golden, "/api/contacts?q=lin")
    fetch(app, golden, "/api/contact?addr=lin@example.test")
    fetch(app, golden, "/api/contact?addr=nobody@example.test")
    fetch(app, golden, "/api/contact?addr=")


def test_documents_and_cleanup(app, golden, world):
    fetch(app, golden, "/api/documents", "GET /api/documents before any body is opened")
    app.get(body_of(world.copy("bea-drafts-01-apple-mail.eml")))
    app.get(body_of(world.copy("ada-inbox-11-html-cid.eml")))
    fetch(app, golden, "/api/documents")
    fetch(app, golden, "/api/documents?limit=nope")
    fetch(app, golden, "/api/cleanup/senders", "GET /api/cleanup/senders with one issue of each newsletter")
    for n, day in ((216, "14"), (217, "21")):           # two older issues of The Weekly Loom arrive
        world.deliver(w.ADA, "INBOX", w.variant(
            "ada-inbox-03-newsletter.eml", Message_ID=f"<issue-{n}@loom.example.test>",
            Date=f"Wed, {day} Oct 2026 07:00:00 +0000", Subject=f"Issue {n}: weaving notes"))
    fetch(app, golden, "/api/cleanup/senders")
    fetch(app, golden, "/api/cleanup/sender?addr=news@loom.example.test")
    fetch(app, golden, "/api/cleanup/sender?addr=deals@spindle.example.test")
    fetch(app, golden, "/api/cleanup/sender?addr=nobody@example.test")
    fetch(app, golden, "/api/cleanup/sender?addr=not-an-address")


# ── search ─────────────────────────────────────────────────────────────────

SEARCHES = (
    "lunch",                                # a bare word
    "\"contract draft\"",                   # a phrase
    "from:lin@example.test",
    "from:lin has:attachment",
    "to:ada",
    "subject:\"planning de novembre\"",
    "body:herringbone",                     # bodies aren't indexed until fetched
    "is:unread",
    "is:flagged",
    "is:answered",
    "is:junk",
    "is:newsletter",
    "is:starred",                           # not an operator: searched as text
    "bucket:spam/scam",
    "category:finance",
    "in:Drafts",
    "in:drafts",                            # folder names match exactly
    f"account:{w.BEA}",
    "after:2026-10-31",
    "before:2026-10-25",
    "-from:lin",                            # negation alone
    "lunch -from:ada",
    "atelier OR harbor",                    # OR is a word, not an operator
    "café",
    "\"unbalanced",
)


@pytest.mark.parametrize("scope", ["all", "account"])
def test_search(app, golden, scope):
    fetch(app, golden, "/api/search?q=")
    for q in SEARCHES:
        fetch(app, golden, f"/api/search?scope={scope}&account={w.ADA}&q={quote(q)}", f"search {scope}: {q}")
    fetch(app, golden, "/api/search?scope=all&q=lunch&limit=1&offset=1")
    fetch(app, golden, "/api/search?scope=all&q=is:unread&limit=0&offset=-5")


# ── one message: body and attachments ───────────────────────────────────────

BODIES = ("ada-inbox-01-lunch.eml", "ada-inbox-06-outlook-invite.eml", "ada-inbox-07-forward-rfc822.eml",
          "ada-inbox-09-rfc2047-folded.eml", "ada-inbox-11-html-cid.eml", "bea-drafts-01-apple-mail.eml",
          "cy-inbox-03-unrelated.eml")


def test_message_bodies(app, golden, world):
    for name in BODIES:
        fetch(app, golden, body_of(world.copy(name)), f"GET body of {name}")
    fetch(app, golden, body_of(world.copy("bea-inbox-01-client.eml"), prefetch="1"),
          "GET body of bea-inbox-01-client.eml as a prefetch")
    fetch(app, golden, body_of({"key": 999999, "folder": "INBOX"}))
    golden.note("UID FETCH commands per account",
                {a: len(world.server.commands_for(a, "UID FETCH")) for a in (w.ADA, w.BEA, w.CY)})


def test_attachments(app, golden, world):
    pdf = world.copy("bea-drafts-01-apple-mail.eml")
    cid = world.copy("ada-inbox-11-html-cid.eml")
    fwd = world.copy("ada-inbox-07-forward-rfc822.eml")
    for copy, name in ((pdf, "bea-drafts-01-apple-mail.eml"), (cid, "ada-inbox-11-html-cid.eml"),
                       (fwd, "ada-inbox-07-forward-rfc822.eml")):
        app.get(body_of(copy))
        for att in app.get(body_of(copy)).json.get("attachments", []):
            fetch(app, golden, attachment_of(copy, att["part_id"]), f"GET attachment {att['part_id']} of {name}")
    fetch(app, golden, attachment_of(pdf, "99"), "GET a part that isn't there")
    fetch(app, golden, attachment_of(pdf, "1x"), "GET a part id that isn't a number")
    fetch(app, golden, attachment_of({"key": 999999, "folder": "INBOX"}, "1"), "GET an attachment of no message")


# ── AI routes with AI off ────────────────────────────────────────────────

def test_ai_routes_without_a_key(app, golden, world, anthropic_api):
    fetch(app, golden, "/api/briefing")
    fetch(app, golden, "/api/review")
    fetch(app, golden, "/api/briefing/since")
    for path in ("/api/briefing", "/api/review", "/api/briefing/since"):
        golden.record(f"POST {path}", app.post(path, {}))
    ahead = "/api/ai/draft-ahead?" + urlencode(world.copy(LUNCH))
    fetch(app, golden, ahead, "GET /api/ai/draft-ahead for the lunch confirmation")
    fetch(app, golden, ahead, "GET /api/ai/draft-ahead for the lunch confirmation without the CSRF token",
          headers={"X-CSRF-Token": None})
    fetch(app, golden, "/api/digest", "GET /api/digest after those")
    golden.note("requests the fake Claude API received", len(anthropic_api.requests))


LUNCH = "ada-inbox-02-lunch-confirm.eml"        # Lin's last word in Ada's lunch thread


@pytest.mark.parametrize("path, body", [
    ("/api/ai/summarize", {"key": LUNCH}),
    ("/api/ai/draft-reply", {"key": LUNCH, "tone": "warm"}),
    ("/api/ai/rewrite", {"text": "can we move lunch to friday", "op": "shorten"}),
    ("/api/ai/ask", {"question": "When is lunch with Lin?"}),
    ("/api/ai/do-plan", {"cmd": "archive the newsletters"}),
    ("/api/briefing/refresh", {}),
    ("/api/review/refresh", {}),
    ("/api/briefing", {}),
    ("/api/briefing/since", {"since": 0}),
    ("/api/review", {})])
def test_without_a_key_every_ai_request_says_ai_is_off(app, world, anthropic_api, capfd, path, body):
    from mailmind.bootstrap.mailbox import store
    lunch = world.pk(LUNCH)
    if body.get("key") == LUNCH:                       # the lunch confirmation, by its key and folder
        body = {**body, **world.copy(LUNCH)}
    c = store.conn()
    try:                                               # triaged, so the brief has something to say
        answers.save_summary(c, lunch, "Lin confirms lunch.", 5, "action-needed", "Reply", "claude-opus-5-5",
                           "2026-11-02T11:00:00+00:00")
        c.commit()
    finally:
        c.close()
    r = app.post(path, body)
    assert (r.status, r.json) == (409, problems.ai_off())
    assert anthropic_api.requests == []
    assert "failed" not in capfd.readouterr().out          # nothing was tried, so nothing failed
    ai_line = app.get("/api/digest").json["ai"]
    assert (ai_line["engine"], ai_line["last_error"]) == ("none", "")


def test_without_a_key_no_mail_leaves_for_the_api(app, world, anthropic_api):
    ahead = "/api/ai/draft-ahead?" + urlencode(world.copy(LUNCH))
    for path in ("/api/briefing", "/api/review", "/api/briefing/since", ahead):
        assert app.get(path).status == 200
    for path in ("/api/briefing", "/api/review", "/api/briefing/since"):
        app.post(path, {})
    assert [r.prompt[:60] for r in anthropic_api.requests] == []
