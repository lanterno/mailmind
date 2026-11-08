"""A preview of a stranger's attachment: only a raster image gets past it, in a document that can run nothing and reach nothing,
and the page's own policy has no word in it for mail."""
import base64
import re
import urllib.parse
from email.message import EmailMessage

import pytest

from fixtures import world as w
from mailmind.content import frame
from mailmind.discovery.domain import documents
from mailmind.platform import perimeter

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>parent.pwned=1</script></svg>'
HTML = b"<script>parent.pwned=1</script>"
POLYGLOT = PNG + HTML

THE_POLICY = ("sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; "
              "frame-ancestors 'self'")


def with_a_file(world, data, filename="photo.png", maintype="image", subtype="png"):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "Dee <dee@example.test>", w.ADA, "A file"
    m["Message-ID"], m["Date"] = f"<preview-{filename}-{len(data)}@example.test>", "Mon, 02 Nov 2026 09:00:00 +0000"
    m.set_content("Attached.")
    m.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    uid = world.deliver(w.ADA, "INBOX", m.as_bytes())
    from mailmind.bootstrap.mailbox import ingest, store
    c = store.conn()
    try:
        row = c.execute("SELECT message_key, pk FROM messages WHERE account=? AND folder='INBOX' AND uid=?", (w.ADA, uid)).fetchone()
    finally:
        c.close()
    ingest.fetch_body(row["pk"])                               # what the backfill does: the body, and what it holds
    c = store.conn()
    try:
        part = c.execute("SELECT part_id FROM attachments WHERE message_pk=?", (row["pk"],)).fetchone()
    finally:
        c.close()
    return {"key": row["message_key"], "folder": "INBOX", "uid": uid, "part_id": part["part_id"]}


def preview(app, names):
    return app.get("/api/message/attachment/preview.html?" + urllib.parse.urlencode(names))


def header(r, name):
    return dict((k.lower(), v) for k, v in r.headers.items())[name.lower()]


def test_an_image_comes_as_a_document_holding_it_and_nothing_else(app, world):
    r = preview(app, with_a_file(world, PNG))
    assert r.status == 200
    assert header(r, "Content-Type") == "text/html; charset=utf-8"
    body = r.body.decode()
    assert f'<img alt="" src="data:image/png;base64,{base64.b64encode(PNG).decode()}">' in body
    assert "<script" not in body.lower()


@pytest.mark.parametrize("data, media", [(PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"), (WEBP, "image/webp")])
def test_each_kind_is_told_by_its_bytes(data, media):
    assert frame.image_type(data) == media


def test_the_type_is_the_bytes_and_not_what_the_sender_called_it(app, world):
    r = preview(app, with_a_file(world, JPEG, "photo.png"))
    assert "data:image/jpeg;base64," in r.body.decode()


@pytest.mark.parametrize("data, filename, maintype, subtype", [
    (SVG, "logo.svg", "image", "svg+xml"),
    (SVG, "logo.png", "image", "png"),                # an SVG calling itself a PNG
    (HTML, "page.png", "image", "png"),               # a web page calling itself a PNG
    (b"%PDF-1.4 x", "a.pdf", "application", "pdf"),
    (b"", "empty.png", "image", "png"),
])
def test_what_isnt_one_of_those_images_is_a_note_and_never_its_own_bytes(app, world, data, filename, maintype, subtype):
    r = preview(app, with_a_file(world, data, filename, maintype, subtype))
    assert r.status == 200
    body = r.body.decode()
    assert "can’t be previewed" in body
    assert "<img" not in body and "<svg" not in body and "<script" not in body
    assert header(r, "Content-Security-Policy") == THE_POLICY


def test_bytes_after_an_images_own_are_data_in_a_data_url_and_cannot_make_a_tag(app, world):
    body = preview(app, with_a_file(world, POLYGLOT)).body.decode()
    assert "<script" not in body
    assert base64.b64encode(POLYGLOT).decode() in body


def test_too_large_an_image_is_a_note_and_not_a_document_that_size(app, world, monkeypatch):
    from mailmind.mailbox.application import bodies
    monkeypatch.setattr(bodies, "PREVIEW_MAX_BYTES", len(PNG) - 1)
    r = preview(app, with_a_file(world, PNG))
    assert "can’t be previewed" in r.body.decode() and "<img" not in r.body.decode()


def test_its_policy_lets_nothing_run_and_it_has_no_origin_to_speak_with(app, world):
    r = preview(app, with_a_file(world, PNG))
    assert header(r, "Content-Security-Policy") == THE_POLICY
    assert not re.search(r"allow-", header(r, "Content-Security-Policy"))
    assert header(r, "X-Frame-Options") == "SAMEORIGIN"
    assert header(r, "X-Content-Type-Options") == "nosniff"


def test_the_pages_own_policy_has_no_word_in_it_for_mail():
    policy = perimeter.page_policy()
    assert "img-src data:;" in policy + ";" and "https:" not in policy and "blob:" not in policy
    assert "script-src 'self';" in policy + ";"


@pytest.mark.parametrize("names", [
    {"key": "x", "folder": "INBOX", "uid": "1", "part_id": "2"},
    {"key": "999999", "folder": "INBOX", "uid": "1", "part_id": "2"},
    {"key": "", "folder": "", "uid": "", "part_id": ""},
])
def test_what_names_no_copy_is_a_document_saying_so(app, names):
    r = preview(app, names)
    assert r.status == 404
    assert "text/html" in header(r, "Content-Type") and header(r, "Content-Security-Policy") == THE_POLICY


def test_a_part_that_wont_read_or_isnt_there_is_a_document_saying_so(app, world):
    names = with_a_file(world, PNG)
    for part_id in ("x", "1\n", "99"):
        r = preview(app, {**names, "part_id": part_id})
        assert r.status == 404 and "can’t be previewed" in r.body.decode()


def test_a_failure_says_so_without_naming_what_went_wrong(app, world, monkeypatch):
    from mailmind.bootstrap import mailbox
    names = with_a_file(world, PNG)
    def boom(*_):
        raise RuntimeError("secret detail")
    monkeypatch.setattr(mailbox.bodies, "attachment", boom)
    r = preview(app, names)
    assert r.status == 500
    assert "secret detail" not in r.body.decode() and "Couldn’t load this file." in r.body.decode()
    assert header(r, "Content-Security-Policy") == THE_POLICY


def test_another_site_cannot_read_a_preview(app, world):
    names = with_a_file(world, PNG)
    r = app.get("/api/message/attachment/preview.html?" + urllib.parse.urlencode(names), headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status == 403


@pytest.mark.parametrize("ct, filename, offered", [
    ("image/png", "a.png", True), ("IMAGE/JPEG; name=x", "a", True), ("image/gif", "", True), ("image/webp", "a.webp", True),
    ("application/octet-stream", "a.PNG", True), (None, "a.jpeg", True),
    ("image/svg+xml", "a.png", False),                      # its type says what it is, whatever it's called
    ("application/octet-stream", "a.svg", False), ("image/heic", "a.heic", False), ("image/bmp", "a.bmp", False),
    ("application/pdf", "a.png", False), ("application/octet-stream", "noextension", False), (None, None, False),
])
def test_a_preview_is_offered_by_what_a_file_says_it_is_and_only_where_its_name_is_all_it_says(ct, filename, offered):
    assert documents.previewable(ct, filename) is offered


def test_the_hub_offers_a_preview_only_of_an_image_of_those_kinds(app, world):
    png = with_a_file(world, PNG)
    svg = with_a_file(world, SVG, "logo.svg", "image", "svg+xml")
    pdf = with_a_file(world, b"%PDF-1.4 x", "a.pdf", "application", "pdf")
    named = {d["filename"]: d for d in app.get("/api/documents?limit=1000").json["docs"]}
    assert named["photo.png"]["preview"] == "/api/message/attachment/preview.html?" + urllib.parse.urlencode(png)
    assert named["logo.svg"]["preview"] is None and named["a.pdf"]["preview"] is None
    assert svg["uid"] != pdf["uid"]
