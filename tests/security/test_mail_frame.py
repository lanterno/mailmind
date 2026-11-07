"""A mail body's own document, what the app's frame loads: its policy, who may frame it,
and what it holds. tests/e2e/browser/test_mail_frame.py holds what a browser makes of it."""
import pytest

from fixtures import world as w
from mailmind.bootstrap.mailbox import store
from mailmind.platform import perimeter

TRACKER = "https://tracker.example.test/open.png"
HOSTILE = f"""From: Mallory <mallory@evil.example.test>
To: Ada Park <ada@example.test>
Subject: Hostile
Date: Sun, 01 Nov 2026 09:00:00 +0000
Message-ID: <hostile@evil.example.test>
MIME-Version: 1.0
Content-Type: text/html; charset=utf-8

<p onclick="parent.pwned=1">Hello</p><script>parent.pwned=1</script><img src="{TRACKER}" alt="tracker">
""".encode()
PLAIN = b"""From: Bo Lin <bo@example.test>
To: Ada Park <ada@example.test>
Subject: Plain
Date: Sun, 01 Nov 2026 09:00:00 +0000
Message-ID: <plain@example.test>
Content-Type: text/plain; charset=utf-8

<script>parent.pwned=1</script> & more

On Monday Ada wrote:
> hello
"""
NO_IMAGES = ("sandbox allow-same-origin; default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; "
             "base-uri 'none'; form-action 'none'; frame-ancestors 'self'")
WITH_IMAGES = NO_IMAGES.replace("img-src data:", "img-src data: https:")


def delivered(world, raw):
    """The pk and key of a message that just arrived, with its body not yet read."""
    uid = world.deliver(w.ADA, "INBOX", raw)
    c = store.conn()
    r = c.execute("SELECT pk, message_key, body_fetched FROM messages WHERE account=? AND folder='INBOX' AND uid=?",
                  (w.ADA, uid)).fetchone()
    c.close()
    assert not r["body_fetched"]
    return r["pk"], r["message_key"]


def key_of(pk):
    c = store.conn()
    r = c.execute("SELECT message_key FROM messages WHERE pk=?", (pk,)).fetchone()
    c.close()
    return r["message_key"]


def document(app, pk, key, images=None, **kw):
    query = f"?key={key}" + (f"&images={images}" if images is not None else "")
    return app.get(f"/api/message/{pk}/body.html{query}", **kw)


def carries_a_frames_headers(r, policy=NO_IMAGES):
    assert r.headers["content-security-policy"] == policy
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert r.headers["content-type"] == "text/html; charset=utf-8"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "no-referrer"
    return True


@pytest.mark.parametrize("images,policy", [(None, NO_IMAGES), ("0", NO_IMAGES), ("1", WITH_IMAGES)])
def test_its_own_policy_lets_remote_images_in_only_when_asked(app, world, images, policy):
    pk = world.pk("ada-inbox-05-receipt.eml")
    r = document(app, pk, key_of(pk), images)
    assert r.status == 200 and carries_a_frames_headers(r, policy)


@pytest.mark.parametrize("site,error", [("cross-site", "cross-site request"), ("same-site", "cross-site request"),
                                        ("none", "same-origin request required"), (None, "same-origin request required")])
def test_only_this_origin_may_ask_for_it(app, world, site, error):
    pk = world.pk("ada-inbox-05-receipt.eml")
    r = document(app, pk, key_of(pk), headers={"Sec-Fetch-Site": site})
    assert (r.status, r.json["error"]) == (403, error)
    assert r.headers["x-frame-options"] == "DENY" and "content-security-policy" not in r.headers


def test_the_page_answers_under_a_policy_of_its_own(app):
    assert app.get("/").headers["content-security-policy"] == perimeter.page_policy()
    assert app.get("/m/ada%40example.test/INBOX").headers["content-security-policy"] == perimeter.page_policy()


def test_no_other_answer_may_be_framed(app, world):
    pk = world.pk("ada-inbox-05-receipt.eml")
    assert app.get(f"/api/message/{pk}/body").headers["x-frame-options"] == "DENY"
    assert app.get("/").headers["x-frame-options"] == "DENY"


def test_it_is_what_the_sanitizer_left(app, world):
    from mailmind.bootstrap.mailbox import ingest
    from mailmind.content import sanitize
    pk, key = delivered(world, HOSTILE)
    assert app.get(f"/api/message/{pk}/body").json["html"] in document(app, pk, key, "0").body.decode()
    for images, tracker in (("0", False), ("1", True)):
        got = document(app, pk, key, images).body.decode()
        assert sanitize.sanitize(ingest.fetch_body(pk)["html"], load_images=tracker)[0] in got
        assert "<script" not in got and "onclick" not in got and "pwned" not in got
        assert (TRACKER in got) is tracker
        assert got.startswith("<!doctype html>")


def test_an_inline_image_comes_inside_it_once_images_are_asked_for(app, world):
    pk = world.pk("ada-inbox-11-html-cid.eml")
    assert b"data:image/png;base64," not in document(app, pk, key_of(pk), "0").body
    assert b"data:image/png;base64," in document(app, pk, key_of(pk), "1").body


def test_plain_text_is_escaped_and_whole(app, world):
    pk, key = delivered(world, PLAIN)
    got = document(app, pk, key).body.decode()
    assert "<pre>&lt;script&gt;parent.pwned=1&lt;/script&gt; &amp; more" in got
    assert "On Monday Ada wrote:\r\n&gt; hello" in got
    assert "<script" not in got


def test_a_message_with_nothing_in_it_says_so(app, world):
    pk, key = delivered(world, PLAIN.split(b"\n\n")[0] + b"\n\n")
    assert b"<pre>(no content)</pre>" in document(app, pk, key).body


def test_a_body_not_stored_yet_is_fetched_and_the_mail_stays_unread(app, world):
    pk, key = delivered(world, PLAIN)
    mark = len(world.server.commands_for(w.ADA, "UID FETCH"))
    assert b"&amp; more" in document(app, pk, key).body
    fetched = [c["line"] for c in world.server.commands_for(w.ADA, "UID FETCH")[mark:]]
    assert len(fetched) == 1 and "BODY.PEEK[]" in fetched[0]
    c = store.conn()
    row = c.execute("SELECT seen, body_fetched FROM messages WHERE pk=?", (pk,)).fetchone()
    c.close()
    assert (row["seen"], row["body_fetched"]) == (0, 1)
    document(app, pk, key)
    assert len(world.server.commands_for(w.ADA, "UID FETCH")) == mark + 1      # stored now, so asked for once


def test_it_writes_nothing_to_the_server(app, world):
    pk, key = delivered(world, HOSTILE)
    mark = world.mark()
    document(app, pk, key, "1")
    assert world.writes(mark) == []


def test_it_asks_nothing_of_the_ai(app, world, ai):
    pk, key = delivered(world, HOSTILE)
    document(app, pk, key)
    document(app, pk, key, "1")
    assert ai.requests == []


@pytest.mark.parametrize("wrong", ["", "0", "abc", "1%20OR%201=1"])
def test_a_key_that_isnt_this_rows_shows_nothing_of_it(app, world, wrong):
    pk, _ = delivered(world, HOSTILE)
    r = app.get(f"/api/message/{pk}/body.html?key={wrong}")
    assert r.status == 404 and carries_a_frames_headers(r)
    assert b"Hello" not in r.body and "isn’t here any more".encode() in r.body


def test_a_pk_that_has_come_to_name_another_message_shows_nothing_of_it(app, world):
    """The page read one message, and its frame asks for that message's key on a pk that names another now."""
    theirs, _ = delivered(world, HOSTILE)
    _, read = delivered(world, PLAIN)
    r = document(app, theirs, read)
    assert r.status == 404 and b"Hello" not in r.body


def test_a_key_alone_cant_say_which_body_since_a_sender_can_reuse_a_message_id(app, world):
    """Why the pk names the copy: two rows in one folder can share a key, each with a body and a sender of its own."""
    first, key = delivered(world, HOSTILE)
    second, same = delivered(world, HOSTILE.replace(b"Hello", b"Not what you read"))
    assert same == key and first != second
    assert b"Hello" in document(app, first, key).body
    assert b"Not what you read" in document(app, second, key).body


def test_no_message_at_all_is_a_document_too(app):
    r = app.get("/api/message/999999/body.html?key=1")
    assert r.status == 404 and carries_a_frames_headers(r)


def test_a_failure_is_a_document_and_says_nothing_of_why(app, world):
    pk, key = delivered(world, PLAIN)
    world.server.drop("UID FETCH", user=w.ADA, times=None)
    r = document(app, pk, key, "1")
    assert r.status == 500 and carries_a_frames_headers(r, WITH_IMAGES)
    assert "Couldn’t load this message.".encode() in r.body and b"abort" not in r.body and b"EOF" not in r.body


@pytest.mark.parametrize("path", ["/api/message/abc/body.html", "/api/message/1/2/body.html", "/api/message//body.html",
                                  "/api/message/99999999999999999999/body.html", "/api/message/1/body.htm"])
def test_only_a_pk_names_one(app, path):
    r = app.get(path)
    assert r.status == 404 and "content-security-policy" not in r.headers and r.headers["x-frame-options"] == "DENY"
