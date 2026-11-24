"""Each number shown beside a list counts that list's rows, over HTTP, on a store with snoozed, muted, threaded mail and
mail on its way out: a folder's unread and total on the rail, each of the Inbox's own views (snoozed, muted, waiting,
reply later) beside its list, and the digest's mail that needs you, which is the Inbox's alone, beside what the brief is
written from. A brief never calls the inbox quiet while mail in it needs you, and a draft MailMind saves is no unread
mail. A sender screened out at the gate, or blocked in Curate, leaves Needs you in every list as in the brief, their
mail kept where it was as junk, until the rule goes."""
import os
from urllib.parse import quote

from fixtures import world as w
from mailmind.bootstrap.mailbox import store

RECEIPT = "ada-inbox-05-receipt.eml"           # unread
LUNCH = "ada-inbox-01-lunch.eml"               # read, answered: its thread holds the confirmation too
CONFIRM = "ada-inbox-02-lunch-confirm.eml"     # unread
NEWSLETTER = "ada-inbox-03-newsletter.eml"     # unread
CI = "ada-inbox-04-ci.eml"                     # read
INVITE = "ada-inbox-06-outlook-invite.eml"     # unread
ARCHIVED = "ada-archive-01-old-thread.eml"
QUIET = "Nothing needs you right now — your inbox is quiet."


def with_server_stopped(app, *statements):
    """Rows another run left, written while the server is stopped."""
    with app.stopped():
        c = store.conn()
        try:
            for sql, args in statements:
                c.execute(sql, args)
            c.commit()
        finally:
            c.close()


def assessed(world, name, importance, action="", category="info"):
    return ("INSERT OR REPLACE INTO assessments(message_key, summary, importance, category, action, model, created_at) "
            "VALUES(?,?,?,?,?,'stub','2026-11-02T11:00:00+00:00')", (world.key(name), "A summary.", importance, category,
                                                                      action))


def a_store_of_every_kind(app, world):
    """Ada's Inbox with a message snoozed, a thread muted (two of its messages in the Inbox) and one on its way out."""
    assert app.post("/api/snooze", {**world.copy(RECEIPT), "preset": "tomorrow"}).status == 200
    assert app.post("/api/mute", world.copy(CONFIRM)).status == 200
    world.server.drop("UID MOVE", user=w.ADA)              # the archive can't reach the server: it waits in the queue
    assert app.post("/api/archive", world.copy(NEWSLETTER)).status == 200


def listed(app, folder, account=w.ADA):
    page = app.get(f"/api/messages?account={account}&folder={quote(folder)}&limit=500").json
    assert not page.get("has_more")
    return page


def folder_counts(app, account=w.ADA):
    return app.get(f"/api/folders?account={account}").json


def test_a_folders_unread_and_total_count_the_rows_its_list_shows(app, world):
    a_store_of_every_kind(app, world)
    shown = {f["name"]: f for f in folder_counts(app)["folders"]}
    for folder in ("INBOX", "Archive", "Drafts"):
        page = listed(app, folder)
        assert (shown[folder]["total"], shown[folder]["unseen"]) == (
            page["total"], sum(1 for r in page["items"] if not r["seen"])), folder
    inbox = {r["key"] for r in listed(app, "INBOX")["items"]}
    assert inbox.isdisjoint({world.key(RECEIPT), world.key(CONFIRM), world.key(LUNCH), world.key(NEWSLETTER)})


def test_a_folder_never_synced_keeps_the_servers_count_though_mailmind_filed_mail_there(app, world):
    world.server.add_folder(w.ADA, "Clients")
    with open(os.path.join(w.CORPUS, ARCHIVED), "rb") as f:
        raw = f.read()
    for n in (1, 2):
        world.server.deliver(w.ADA, "Clients", raw.replace(b"Message-ID: <", b"Message-ID: <client%d." % n))
    assert app.post("/api/move", {**world.copy(RECEIPT), "to": "Clients"}).status == 200
    clients = next(f for f in folder_counts(app)["folders"] if f["name"] == "Clients")
    assert (clients["total"], clients["unseen"]) == (3, 3)       # the server's, not the one row MailMind keeps
    app.work("sync", w.ADA, "Clients")
    page = listed(app, "Clients")
    clients = next(f for f in folder_counts(app)["folders"] if f["name"] == "Clients")
    assert (clients["total"], clients["unseen"]) == (page["total"], sum(1 for r in page["items"] if not r["seen"])) == (3, 3)


def test_a_draft_mailmind_saves_is_never_counted_unread(app, world):
    assert app.post("/api/draft", {"account": w.ADA, "mode": "new", "to": ["Lin Mori <lin@example.test>"],
                                   "subject": "Fabric order (draft)", "text": "Hi Lin,\n\nThe linen came in."}).status == 200
    app.work("sync", w.ADA, "Drafts")
    page = listed(app, "Drafts")
    drafts = next(f for f in folder_counts(app)["folders"] if f["name"] == "Drafts")
    assert "Fabric order (draft)" in [r["subject"] for r in page["items"]]
    assert (drafts["total"], drafts["unseen"]) == (page["total"], 0)
    assert all(r["seen"] for r in page["items"])


def test_the_inboxs_views_count_the_rows_their_lists_show(app, world):
    a_store_of_every_kind(app, world)
    thread = world.row(INVITE)["thread_id"]
    assert app.post("/api/replylater", {"account": w.ADA, "thread_id": thread}).status == 200
    lunch = world.row(LUNCH)["thread_id"]
    ci = world.row(CI)["thread_id"]
    with_server_stopped(app, *[
        ("INSERT OR REPLACE INTO followups(account, thread_id, kind, state, first_sent_ts, last_msg_ts, due_ts, created_at) "
         "VALUES(?,?,?,'due',1793500000,1793500000,1793600000,'2026-11-01T00:00:00+00:00')", (w.ADA, t, kind))
        for t, kind in ((ci, "sent"), (lunch, "received"))])
    counts = folder_counts(app)
    for view, name in (("snoozed", "__snoozed__"), ("muted", "__muted__"), ("waiting", "__waiting__"),
                       ("replylater", "__replylater__")):
        assert counts[view] == len(listed(app, name)["items"]), view
    assert (counts["snoozed"], counts["muted"], counts["replylater"]) == (1, 1, 1)


def needs_you(digest):
    return sorted(it["key"] for it in digest["items"] if it["needs_you"])


def test_what_needs_you_is_the_inboxs_and_the_brief_is_written_from_it(app, world):
    with_server_stopped(app, assessed(world, CONFIRM, 1, action="Confirm the table for four"),
                        assessed(world, INVITE, 4), assessed(world, ARCHIVED, 5, action="Reply about the cabin"),
                        assessed(world, CI, 1))
    digest = app.get("/api/digest").json
    assert {it["folder"] for it in digest["items"]} == {"INBOX"}
    assert needs_you(digest) == sorted([world.key(CONFIRM), world.key(INVITE)])
    brief = app.get("/api/briefing").json
    assert brief["narrative"] != QUIET and brief.get("missing")
    # Filed, a message that needs you leaves the digest with it, wherever it is kept now.
    assert app.post("/api/archive", world.copy(INVITE)).status == 200
    assert needs_you(app.get("/api/digest").json) == [world.key(CONFIRM)]


def test_mail_that_needs_you_at_the_lowest_importance_is_never_called_quiet(app, world):
    # Every other message read and rated as nothing to glance at: mail still waiting for its summary isn't quiet either.
    read_rest = ("INSERT OR IGNORE INTO assessments(message_key, summary, importance, category, action, model, created_at) "
                 "SELECT DISTINCT message_key, 'A summary.', 1, 'info', '', 'stub', '2026-11-02T11:00:00+00:00' "
                 "FROM messages WHERE message_key IS NOT NULL", ())
    with_server_stopped(app, assessed(world, CONFIRM, 1, action="Confirm the table for four"), read_rest)
    assert needs_you(app.get("/api/digest").json) == [world.key(CONFIRM)]
    assert app.get("/api/briefing").json["narrative"] != QUIET
    assert app.post("/api/mute", world.copy(CONFIRM)).status == 200
    assert needs_you(app.get("/api/digest").json) == []
    assert app.get("/api/briefing").json["narrative"] == QUIET


CAFE = "ada-inbox-10-raw-utf8.eml"                                               # the café's, a sender at the gate
NOORS = ("ada-inbox-06-outlook-invite.eml", "ada-inbox-07-forward-rfc822.eml")   # from studio.example.test


def what_needs_you_everywhere(app):
    """The keys of what needs you in Ada's Inbox, which the brief (and the rail, which counts it) and each list of it
    must agree on: her Inbox, the Inboxes, the Inboxes as conversations, and a search of her Inbox."""
    digest = app.get("/api/digest").json
    brief = needs_you(digest)
    ada = sorted(r["key"] for r in listed(app, "INBOX")["items"] if r["needs_you"])
    assert ada == sorted(it["key"] for it in digest["items"] if it["needs_you"] and it["account"] == w.ADA) == brief
    inboxes = app.get("/api/inboxes?limit=500").json
    assert not inboxes["has_more"]
    assert sorted(r["key"] for r in inboxes["items"] if r["needs_you"]) == brief
    talks = app.get("/api/inboxes?group=thread&limit=500").json["items"]
    assert {r["key"] for r in talks if r["needs_you"]} <= set(brief)
    found = app.get(f"/api/search?scope=all&account={w.ADA}&q=in:INBOX").json["items"]
    assert sorted(r["key"] for r in found if r["needs_you"]) == brief
    return brief


def gate(app):
    return {s["from_addr"] for s in app.get(f"/api/screener?account={w.ADA}").json["items"]}


def test_a_sender_screened_out_leaves_needs_you_in_every_list_as_in_the_brief_and_comes_back_with_the_rule(app, world):
    with_server_stopped(app, assessed(world, CAFE, 4, action="Book the table"), assessed(world, INVITE, 4),
                        assessed(world, CONFIRM, 1, action="Confirm the table for four"))
    cafe = world.key(CAFE)
    before = what_needs_you_everywhere(app)
    assert before == sorted([cafe, world.key(INVITE), world.key(CONFIRM)])
    assert "hello@cafe.example.test" in gate(app)
    assert app.post("/api/rules", {"token": "hello@cafe.example.test", "verdict": "block"}).status == 200
    # Left where it was, read as blocked as their next message will be: out of Needs you and the brief, and the gate.
    assert what_needs_you_everywhere(app) == sorted(set(before) - {cafe})
    row = next(r for r in listed(app, "INBOX")["items"] if r["key"] == cafe)
    assert (row["is_junk"], row["bucket"], row["action"]) == (True, "blocked", "Book the table")
    assert "hello@cafe.example.test" not in gate(app)
    # The rule taken back in Curate, what the AI said of it needs you again.
    assert app.post("/api/rules", {"token": "hello@cafe.example.test", "verdict": "remove"}).status == 200
    assert what_needs_you_everywhere(app) == before
    assert "hello@cafe.example.test" in gate(app)


def test_a_domain_blocked_in_curate_leaves_needs_you_in_every_list_as_in_the_brief_and_comes_back_with_the_rule(app, world):
    with_server_stopped(app, *(assessed(world, name, 4, action="Answer Noor") for name in NOORS),
                        assessed(world, CONFIRM, 1, action="Confirm the table for four"))
    before = what_needs_you_everywhere(app)
    assert before == sorted([*(world.key(name) for name in NOORS), world.key(CONFIRM)])
    assert app.post("/api/rules", {"token": "studio.example.test", "verdict": "block"}).status == 200
    assert what_needs_you_everywhere(app) == [world.key(CONFIRM)]
    assert app.post("/api/rules", {"token": "studio.example.test", "verdict": "remove"}).status == 200
    assert what_needs_you_everywhere(app) == before
