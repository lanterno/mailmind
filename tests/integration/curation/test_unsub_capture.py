"""Cleanup can offer an unsubscribe for any sender. Ingest keeps a message's unsubscribe headers, mail
from before that has them read off the server on demand, and what was read outlives the sender's mail,
so trying again after the sweep still knows the way."""
import pytest

from fixtures import world as w
from mailmind.bootstrap.curation import safe_http
from mailmind.curation.application import unsubscribe
from mailmind.bootstrap import curation

NEWS = "ada-inbox-03-newsletter.eml"
LOOM = "news@loom.example.test"
LUNCH = "ada-inbox-02-lunch-confirm.eml"


@pytest.fixture
def posts(monkeypatch):
    sent = []
    monkeypatch.setattr(safe_http, "one_click_post", lambda url, _redirects=1: (sent.append(url), (True, 200, None))[1])
    monkeypatch.setattr(unsubscribe.Pace, "wait_turn", lambda self: None)
    return sent


def row(world, name=NEWS, folder="INBOX"):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return c.execute("SELECT * FROM messages WHERE account=? AND folder=? AND message_id=?",
                         (w.ADA, folder, w.message_id(name))).fetchone()
    finally:
        c.close()


def forget_headers(world, name=NEWS):
    """The row as an older commit left it: nothing kept of its headers, and no body."""
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    pk = row(world, name)["pk"]
    c.execute("UPDATE messages SET list_unsub_raw=NULL, list_unsub_method=NULL WHERE pk=?", (pk,))
    c.execute("DELETE FROM bodies WHERE message_pk=?", (pk,))
    c.commit()
    c.close()


def method(app, addr=LOOM):
    return app.get(f"/api/cleanup/sender?addr={addr}").json["unsub"]["method"]


def test_ingest_keeps_a_messages_unsubscribe_headers(world):
    r = row(world)
    assert r["list_unsub_method"] == "one-click"
    assert r["list_unsub_raw"].startswith("<https://loom.example.test/u/ada-218>")
    assert r["list_unsub_raw"].endswith(" | POST:list-unsubscribe=one-click")


def test_ingest_says_so_when_there_are_none(world):
    assert (row(world, LUNCH)["list_unsub_method"], row(world, LUNCH)["list_unsub_raw"]) == ("none", "")


def test_a_header_folded_across_lines_is_read_whole(world):
    from mailmind.bootstrap.mailbox import ingest
    from mailmind.bootstrap import accounts
    raw = (b"From: News <folded@list.example.test>\r\nTo: ada@example.test\r\nSubject: Folded\r\n"
           b"Message-ID: <folded@example.test>\r\nDate: Mon, 02 Nov 2026 09:00:00 +0000\r\n"
           b"List-Unsubscribe: <mailto:out@list.example.test>,\r\n <https://list.example.test/u/1>\r\n"
           b"List-Unsubscribe-Post: List-Unsubscribe=\r\n One-Click\r\n"
           b"Content-Type: text/plain\r\n\r\nHello.\r\n")
    world.server.deliver(w.ADA, "INBOX", raw)
    ingest.sync_new(accounts.get(w.ADA))
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    r = c.execute("SELECT list_unsub_method FROM messages WHERE message_id='<folded@example.test>'").fetchone()
    c.close()
    assert r["list_unsub_method"] == "one-click"


def test_a_sender_with_no_stored_body_can_be_unsubscribed_from(app, world):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    c.execute("DELETE FROM bodies WHERE message_pk=?", (row(world)["pk"],))
    c.commit()
    c.close()
    since = world.mark()
    assert method(app) == "one-click"
    assert world.writes(since) == []


def test_mail_from_before_ingest_kept_them_is_read_off_the_server(app, world):
    forget_headers(world)
    assert method(app) == "one-click"
    r = row(world)
    assert r["list_unsub_method"] == "one-click" and r["list_unsub_raw"]


def test_that_read_takes_the_headers_alone(app, world):
    forget_headers(world)
    before = len(world.server.commands)
    method(app)
    fetches = [l for l in world.server.commands[before:] if "FETCH" in str(l).upper()]
    assert fetches and all("LIST-UNSUBSCRIBE" in str(f).upper() and "BODY.PEEK[]" not in str(f).upper() for f in fetches)


def test_it_reads_once(app, world):
    forget_headers(world)
    method(app)
    before = len(world.server.commands)
    method(app)
    assert not [l for l in world.server.commands[before:] if "FETCH" in str(l).upper()]


def test_with_the_server_away_a_preview_still_answers_and_nothing_is_cached(app, world):
    forget_headers(world)
    world.server.fail("UID FETCH", user=w.ADA, times=None)
    assert method(app) == "none"
    assert row(world)["list_unsub_method"] is None
    world.server.clear_faults()
    assert method(app) == "one-click"


def test_a_busy_gate_is_waited_for_only_so_long(app, world, monkeypatch):
    from gate import held_elsewhere
    forget_headers(world)
    monkeypatch.setattr(curation, "UNSUB_READ_WAIT", 0.05)
    with held_elsewhere(w.ADA):
        assert method(app) == "none"
    assert row(world)["list_unsub_method"] is None


def test_the_newest_message_decides(app, world):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    newer = w.variant(NEWS, Message_ID="<issue-219@loom.example.test>", Date="Mon, 02 Nov 2026 11:00:00 +0000",
                      List_Unsubscribe="<mailto:out@loom.example.test>", List_Unsubscribe_Post="")
    world.server.deliver(w.ADA, "INBOX", newer)
    ingest.sync_new(accounts.get(w.ADA))
    assert method(app) == "mailto"


def test_trying_again_after_the_sweep_still_knows_the_way(app, world, posts):
    r1 = app.get(f"/api/cleanup/sender?addr={LOOM}").json
    real = safe_http.one_click_post
    safe_http.one_click_post = lambda url, _redirects=1: (False, 500, "nope")
    try:
        first = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True,
                                                  "confirm_token": r1["confirm_token"]}).json
    finally:
        safe_http.one_click_post = real
    assert first["status"] == "failed" and first["swept"] >= 1
    assert row(world) is None                                     # the sweep took its row
    again = app.post("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True,
                                              "confirm_token": r1["confirm_token"]}).json
    assert again["status"] == "unsubscribed_oneclick"
    assert posts == ["https://loom.example.test/u/ada-218"]


def test_a_list_that_stops_offering_a_way_is_taken_at_its_word(app, world):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    from mailmind.bootstrap.mailbox import store
    assert method(app) == "one-click"
    c = store.conn()
    assert curation.sqlite.sender_unsub_get(c, LOOM) is not None
    c.close()
    newer = w.variant(LUNCH, Message_ID="<issue-220@loom.example.test>", Date="Mon, 02 Nov 2026 11:00:00 +0000",
                      From=f"News <{LOOM}>")
    world.server.deliver(w.ADA, "INBOX", newer)
    ingest.sync_new(accounts.get(w.ADA))
    assert method(app) == "none"
    c = store.conn()
    assert curation.sqlite.sender_unsub_get(c, LOOM) is None
    c.close()


def test_a_kept_newsletter_goes_back_to_being_a_newsletter_when_the_rule_goes(app, world):
    # Its List-Unsubscribe is all that says it's one, so that has to have been kept.
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    from mailmind.bootstrap.mailbox import store
    addr = "pat@plain.example.test"
    world.server.deliver(w.ADA, "INBOX", (f"From: Pat <{addr}>\r\nTo: ada@example.test\r\nSubject: Monthly thoughts\r\n"
                                          "Message-ID: <plain@example.test>\r\nDate: Mon, 02 Nov 2026 09:00:00 +0000\r\n"
                                          "List-Unsubscribe: <mailto:out@plain.example.test>\r\n"
                                          "Content-Type: text/plain\r\n\r\nHello.\r\n").encode())
    ingest.sync_new(accounts.get(w.ADA))

    def plain():
        c = store.conn()
        try:
            return c.execute("SELECT bucket, is_junk FROM messages WHERE message_id='<plain@example.test>'").fetchone()
        finally:
            c.close()
    before = tuple(plain())
    assert before[1] == 1
    assert app.post("/api/rules", {"token": addr, "verdict": "keep"}).json["ok"]
    assert plain()["is_junk"] == 0
    assert app.post("/api/rules", {"token": addr, "verdict": "remove"}).json["ok"]
    assert tuple(plain()) == before
