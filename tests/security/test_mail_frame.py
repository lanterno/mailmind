"""A mail body's own document, what the app's frame loads: its policy, who may frame it,
and what it holds. tests/e2e/browser/test_mail_frame.py holds what a browser makes of it."""
import html
import urllib.parse

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
    """The pk and key of a message that just arrived in Ada's INBOX, with its body not yet read."""
    uid = world.deliver(w.ADA, "INBOX", raw)
    c = store.conn()
    r = c.execute("SELECT pk, message_key, body_fetched FROM messages WHERE account=? AND folder='INBOX' AND uid=?",
                  (w.ADA, uid)).fetchone()
    c.close()
    assert not r["body_fetched"]
    return r["pk"], r["message_key"]


def document(app, key, folder="INBOX", images=None, **kw):
    """The frame's document of the copy `key` and `folder` name."""
    args = {"key": key, "folder": folder, **({"images": images} if images is not None else {})}
    return app.get(f"/api/message/body.html?{urllib.parse.urlencode(args)}", **kw)


def opened(app, key, folder="INBOX"):
    """What the reader reads of the same copy, as JSON."""
    return app.get(f"/api/message/body?{urllib.parse.urlencode({'key': key, 'folder': folder})}")


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
    receipt = world.copy("ada-inbox-05-receipt.eml")
    r = document(app, receipt["key"], receipt["folder"], images)
    assert r.status == 200 and carries_a_frames_headers(r, policy)


@pytest.mark.parametrize("site,error", [("cross-site", "cross-site request"), ("same-site", "cross-site request"),
                                        ("none", "same-origin request required"), (None, "same-origin request required")])
def test_only_this_origin_may_ask_for_it(app, world, site, error):
    receipt = world.copy("ada-inbox-05-receipt.eml")
    r = document(app, receipt["key"], receipt["folder"], headers={"Sec-Fetch-Site": site})
    assert (r.status, r.json["detail"]) == (403, error)
    assert r.headers["x-frame-options"] == "DENY" and "content-security-policy" not in r.headers


def test_the_page_answers_under_a_policy_of_its_own(app):
    assert app.get("/").headers["content-security-policy"] == perimeter.page_policy()
    assert app.get("/m/ada%40example.test/INBOX").headers["content-security-policy"] == perimeter.page_policy()


def test_no_other_answer_may_be_framed(app, world):
    r = opened(app, world.key("ada-inbox-05-receipt.eml"))
    assert r.status == 200 and r.headers["x-frame-options"] == "DENY"
    assert app.get("/").headers["x-frame-options"] == "DENY"


def test_it_is_what_the_sanitizer_left(app, world):
    from mailmind.bootstrap.mailbox import ingest
    from mailmind.content import sanitize
    pk, key = delivered(world, HOSTILE)
    assert opened(app, key).json["html"] in document(app, key, images="0").body.decode()
    for images, tracker in (("0", False), ("1", True)):
        got = document(app, key, images=images).body.decode()
        assert sanitize.sanitize(ingest.fetch_body(pk)["html"], load_images=tracker)[0] in got
        assert "<script" not in got and "onclick" not in got and "pwned" not in got
        assert (TRACKER in got) is tracker
        assert got.startswith("<!doctype html>")


def test_an_inline_image_comes_inside_it_once_images_are_asked_for(app, world):
    key = world.key("ada-inbox-11-html-cid.eml")
    assert b"data:image/png;base64," not in document(app, key, images="0").body
    assert b"data:image/png;base64," in document(app, key, images="1").body


def test_plain_text_is_escaped_and_whole(app, world):
    _, key = delivered(world, PLAIN)
    got = document(app, key).body.decode()
    assert "<pre>&lt;script&gt;parent.pwned=1&lt;/script&gt; &amp; more" in got
    assert "On Monday Ada wrote:\r\n&gt; hello" in got
    assert "<script" not in got


def test_a_message_with_nothing_in_it_says_so(app, world):
    _, key = delivered(world, PLAIN.split(b"\n\n")[0] + b"\n\n")
    assert b"<pre>(no content)</pre>" in document(app, key).body


def test_a_body_not_stored_yet_is_fetched_and_the_mail_stays_unread(app, world):
    pk, key = delivered(world, PLAIN)
    mark = len(world.server.commands_for(w.ADA, "UID FETCH"))
    assert b"&amp; more" in document(app, key).body
    fetched = [c["line"] for c in world.server.commands_for(w.ADA, "UID FETCH")[mark:]]
    assert len(fetched) == 1 and "BODY.PEEK[]" in fetched[0]
    c = store.conn()
    row = c.execute("SELECT seen, body_fetched FROM messages WHERE pk=?", (pk,)).fetchone()
    c.close()
    assert (row["seen"], row["body_fetched"]) == (0, 1)
    document(app, key)
    assert len(world.server.commands_for(w.ADA, "UID FETCH")) == mark + 1      # stored now, so asked for once


def test_it_writes_nothing_to_the_server(app, world):
    _, key = delivered(world, HOSTILE)
    mark = world.mark()
    document(app, key, images="1")
    assert world.writes(mark) == []


def test_it_asks_nothing_of_the_ai(app, world, ai):
    _, key = delivered(world, HOSTILE)
    document(app, key)
    document(app, key, images="1")
    assert ai.requests == []


def names_no_copy(r):
    """The frame's own 404 document, with nothing of any message in it."""
    assert r.status == 404 and carries_a_frames_headers(r)
    assert b"Hello" not in r.body and "isn’t here any more".encode() in r.body
    return True


@pytest.mark.parametrize("wrong", ["", "0", "abc", "1 OR 1=1", "-1", "1.0", " 1", "\u0661", "99999999999999999999999"])
def test_a_key_that_names_no_copy_or_wont_read_shows_nothing(app, world, wrong):
    delivered(world, HOSTILE)
    assert names_no_copy(document(app, wrong))


def test_a_key_with_noughts_before_it_shows_what_the_reader_opens(app, world):
    """The frame reads the key as the body route does, so the two never disagree on which copy a key names."""
    _, key = delivered(world, HOSTILE)
    padded = f"000{key}"
    assert opened(app, padded).status == 200
    got = document(app, padded)
    assert got.status == 200 and carries_a_frames_headers(got)
    assert opened(app, padded).json["html"] in got.body.decode() and got.body == document(app, key).body


@pytest.mark.parametrize("folder", ["", "Archive", "INBOX/", "INBOX' OR '1'='1", "%"])
def test_a_key_shows_nothing_from_a_folder_it_has_no_copy_in(app, world, folder):
    _, key = delivered(world, HOSTILE)
    assert names_no_copy(document(app, key, folder))


def test_a_key_or_a_folder_left_out_is_a_document_too(app, world):
    _, key = delivered(world, HOSTILE)
    assert names_no_copy(app.get(f"/api/message/body.html?key={key}"))
    assert names_no_copy(app.get("/api/message/body.html?folder=INBOX"))
    assert names_no_copy(app.get("/api/message/body.html"))


def test_a_renumber_leaves_the_frame_on_the_message_the_page_read(app, world):
    """The page read a message by key and folder: a new UIDVALIDITY gives its row a new pk, and its old pk may come to
    name another message, but the frame shows the message the page read and no other."""
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    theirs, key = delivered(world, HOSTILE)
    _, read = delivered(world, PLAIN)
    world.server.renumber(w.ADA, "INBOX")
    ingest.sync_new(accounts.get(w.ADA))
    assert w.copy_of(theirs) != {"key": key, "folder": "INBOX"}        # the row the page saw is no longer this message's
    assert b"Hello" in document(app, key).body
    mine = document(app, read)
    assert mine.status == 200 and b"&amp; more" in mine.body and b"Hello" not in mine.body


def test_the_key_of_another_accounts_message_shows_only_that_message(app, world):
    """A key is one account's and one Message-ID's: Bea's INBOX copy is hers, never a body of Ada's that shares a folder
    name."""
    _, key = delivered(world, HOSTILE)
    bea = world.copy("bea-inbox-01-client.eml")
    assert bea["folder"] == "INBOX" and bea["key"] != key
    r = document(app, bea["key"], bea["folder"])
    assert r.status == 200 and b"Hello" not in r.body
    first_line = opened(app, bea["key"]).json["text"].strip().splitlines()[0]
    assert html.escape(first_line, quote=False) in r.body.decode()


def test_two_copies_in_one_folder_under_one_key_show_the_one_the_reader_opens(app, world):
    """A sender can reuse a Message-ID, so two rows in one folder can share a key, each with a body of its own. The key and
    folder then name one: a listed one first, then the newer, and the frame and the reader take the same."""
    first, key = delivered(world, HOSTILE)
    second, same = delivered(world, HOSTILE.replace(b"Hello", b"Not what you read"))
    assert same == key and first != second
    got = document(app, key).body.decode()
    assert "Not what you read" in got and "Hello" not in got
    assert opened(app, key).json["html"] in got
    c = store.conn()
    c.execute("UPDATE messages SET pending_removal=1 WHERE pk=?", (second,))     # on its way out, so not listed
    c.commit()
    c.close()
    got = document(app, key).body.decode()
    assert "Hello" in got and "Not what you read" not in got
    assert opened(app, key).json["html"] in got


def test_no_message_at_all_is_a_document_too(app):
    assert names_no_copy(document(app, 999999))


def test_a_failure_is_a_document_and_says_nothing_of_why(app, world):
    _, key = delivered(world, PLAIN)
    world.server.drop("UID FETCH", user=w.ADA, times=None)
    r = document(app, key, images="1")
    assert r.status == 500 and carries_a_frames_headers(r, WITH_IMAGES)
    assert "Couldn’t load this message.".encode() in r.body and b"abort" not in r.body and b"EOF" not in r.body


@pytest.mark.parametrize("path", ["/api/message/{pk}/body.html?key={key}", "/api/message/{pk}/body.html",
                                  "/api/message/abc/body.html", "/api/message/1/2/body.html", "/api/message//body.html",
                                  "/api/message/99999999999999999999/body.html", "/api/message/body.htm?key={key}&folder=INBOX"])
def test_no_other_path_answers_with_a_frames_document(app, world, path):
    """The old path by pk is gone: it and anything like it is the plain 404, which no frame may show."""
    pk, key = delivered(world, HOSTILE)
    r = app.get(path.format(pk=pk, key=key))
    assert r.status == 404 and "content-security-policy" not in r.headers and r.headers["x-frame-options"] == "DENY"
    assert b"Hello" not in (r.body if isinstance(r.body, bytes) else repr(r.body).encode())
