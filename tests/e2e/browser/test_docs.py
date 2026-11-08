"""The Document Hub: every attachment across accounts, filtered and paged by the server."""
import re
import struct
import zlib
from email.message import EmailMessage

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import expect

from fixtures import world as w

pytestmark = pytest.mark.browser

RECEIPT = "ada-inbox-05-receipt.eml"


# Axe judges contrast as it finds it, so fade-ins must end first. A looping animation never ends: waiting for one hangs
# for good, since evaluate has no timeout.
FADES_DONE = """Promise.all(document.getAnimations()
    .filter(a => a.effect.getComputedTiming().iterations !== Infinity)
    .map(a => a.finished.catch(() => null)))"""


def clean(page):
    assert page.problems == [], page.problems
    assert page.blocked == [], page.blocked


def hub_files(world, n):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        for i in range(n):
            c.execute("INSERT INTO attachments(message_pk, part_id, filename, content_type, size, is_inline) "
                      "VALUES (?,?,?,'application/pdf',1000,0)", (world.pk(RECEIPT), f"f.{i}", f"report-{i:03}.pdf"))
        c.commit()
    finally:
        c.close()


def one_file(world, name, filename, part="one", kind="application/pdf"):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        c.execute("INSERT INTO attachments(message_pk, part_id, filename, content_type, size, is_inline) "
                  "VALUES (?,?,?,?,1000,0)", (world.pk(name), part, filename, kind))
        c.commit()
    finally:
        c.close()


def docs(page):
    page.goto(f"/m/{w.ADA}/INBOX")
    expect(page.locator("#monos")).not_to_be_empty()
    page.keyboard.press("ControlOrMeta+k")
    page.get_by_role("combobox", name="Command").fill("Documents")
    page.keyboard.press("Enter")
    expect(page.get_by_role("dialog", name="Documents")).to_be_visible()
    return page


def matched(page):
    return page.evaluate("fetch('/api/documents?limit=1').then(r => r.json()).then(d => d.matched)")


def a_png(side=24):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    pixels = b"".join(b"\x00" + b"\xd0\x30\x30" * side for _ in range(side))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


def a_stored_file(world, data, filename, maintype, subtype):
    """A message that really holds the file, with its body stored as the backfill would."""
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "Dee <dee@example.test>", w.ADA, "A file for you"
    m["Message-ID"], m["Date"] = f"<hub-{filename}@example.test>", "Mon, 02 Nov 2026 09:00:00 +0000"
    m.set_content("Attached.")
    m.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    uid = world.deliver(w.ADA, "INBOX", m.as_bytes())
    from mailmind.bootstrap.mailbox import ingest, store
    c = store.conn()
    try:
        pk = c.execute("SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND uid=?", (w.ADA, uid)).fetchone()["pk"]
    finally:
        c.close()
    ingest.fetch_body(pk)


def test_an_image_previews_in_place_in_a_frame_that_can_do_nothing(page, world):
    a_stored_file(world, a_png(), "photo.png", "image", "png")
    docs(page)
    button = page.get_by_role("button", name="Preview photo.png")
    expect(button).to_have_attribute("aria-expanded", "false")
    with page.pw.expect_response(re.compile(r"/api/message/attachment/preview\.html")) as answer:
        button.click()
    assert answer.value.status == 200
    frame = page.locator("iframe.doc-preview")
    expect(frame).to_have_attribute("sandbox", "")
    expect(frame).to_have_attribute("title", "Preview of photo.png")
    expect(button).to_have_attribute("aria-expanded", "true")
    assert page.pw.evaluate("document.querySelector('iframe.doc-preview').contentDocument") is None   # no origin to share
    expect(page.frame_locator("iframe.doc-preview").locator("img")).to_be_visible(timeout=5000)
    button.click()
    expect(frame).to_have_count(0)
    clean(page)


def test_a_file_that_isnt_an_image_offers_no_preview(page, world):
    a_stored_file(world, b"%PDF-1.4 x", "paper.pdf", "application", "pdf")
    a_stored_file(world, b'<svg xmlns="http://www.w3.org/2000/svg"/>', "logo.svg", "image", "svg+xml")
    docs(page)
    expect(page.locator(".doc-row", has_text="paper.pdf")).to_have_count(1)
    expect(page.get_by_role("button", name=re.compile("Preview"))).to_have_count(0)
    clean(page)


def test_only_one_image_previews_at_a_time(page, world):
    a_stored_file(world, a_png(), "one.png", "image", "png")
    a_stored_file(world, a_png(30), "two.png", "image", "png")
    docs(page)
    page.get_by_role("button", name="Preview one.png").click()
    expect(page.locator("iframe.doc-preview")).to_have_attribute("title", "Preview of one.png")
    page.get_by_role("button", name="Preview two.png").click()
    expect(page.locator("iframe.doc-preview")).to_have_count(1)
    expect(page.locator("iframe.doc-preview")).to_have_attribute("title", "Preview of two.png")
    clean(page)


def test_the_hub_shows_more_and_keeps_your_place(page, world):
    hub_files(world, 450)
    docs(page)
    rows = page.locator("#docList .doc-row")
    expect(rows).to_have_count(200)
    more = page.get_by_role("button", name=re.compile(r"Show more · \d+ left"))
    more.click()
    expect(rows).to_have_count(400)
    expect(page.locator("#docList .doc-dl").nth(200)).to_be_focused()
    more.click()
    expect(rows).to_have_count(matched(page))
    expect(more).to_have_count(0)
    names = rows.locator(".doc-fn").all_text_contents()
    assert len(names) == len(set(names))
    clean(page)


def test_the_hub_searches_files_it_has_not_loaded(page, world):
    hub_files(world, 450)
    one_file(world, "ada-inbox-09-rfc2047-folded.eml", "needle.pdf")
    docs(page)
    expect(page.locator("#docList .doc-row")).to_have_count(200)
    page.locator("#docSearch").fill("needle")
    expect(page.locator("#docList .doc-fn")).to_have_text(["needle.pdf"])
    expect(page.locator("#docCount")).to_have_text("· 1")
    page.locator("#docSearch").fill("nothing-is-called-this")
    expect(page.locator("#docList .doc-empty")).to_have_text("No files match.")
    clean(page)


def test_the_hub_shows_a_file_once_when_new_mail_moves_the_list(page, world):
    hub_files(world, 250)
    docs(page)
    rows = page.locator("#docList .doc-row")
    expect(rows).to_have_count(200)
    every = matched(page)
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        newer = c.execute("SELECT pk FROM messages WHERE ts > (SELECT ts FROM messages WHERE pk=?) ORDER BY ts DESC LIMIT 1",
                          (world.pk(RECEIPT),)).fetchone()["pk"]
        c.execute("INSERT INTO attachments(message_pk, part_id, filename, content_type, size, is_inline) "
                  "VALUES (?,'new','arrived.pdf','application/pdf',1000,0)", (newer,))
        c.commit()
    finally:
        c.close()
    page.get_by_role("button", name=re.compile(r"Show more")).click()
    expect(rows).to_have_count(every)                                   # all it had, each once
    expect(page.get_by_role("button", name=re.compile(r"Show more|Loading"))).to_have_count(0)
    names = rows.locator(".doc-fn").all_text_contents()
    assert len(names) == len(set(names))
    clean(page)


def test_a_slow_answer_does_not_overwrite_a_newer_search(page, world):
    hub_files(world, 3)
    docs(page)
    expect(page.locator("#docList .doc-fn").first).to_be_visible()
    held = []
    slow = re.compile(r"/api/documents\?.*q=report")
    page.pw.route(slow, lambda route: held.append(route))
    page.locator("#docSearch").fill("report")
    while not held:
        page.wait_for_timeout(50)
    page.locator("#docSearch").fill("nothing-is-called-this")
    expect(page.locator("#docList .doc-empty")).to_have_text("No files match.")
    with page.pw.expect_response(slow):
        held[0].continue_()
    page.wait_for_timeout(100)
    expect(page.locator("#docList .doc-empty")).to_have_text("No files match.")
    clean(page)


def test_when_showing_more_fails_the_hub_keeps_what_it_has(page, world):
    hub_files(world, 250)
    docs(page)
    rows = page.locator("#docList .doc-row")
    expect(rows).to_have_count(200)
    page.pw.route(re.compile(r"/api/documents\?.*offset=200"), lambda route: route.fulfill(status=500, body="{}"))
    page.get_by_role("button", name=re.compile(r"Show more")).click()
    expect(page.locator("#toast")).to_have_text("Couldn’t load more files.")
    expect(rows).to_have_count(200)
    every = matched(page)
    page.pw.unroute(re.compile(r"/api/documents\?.*offset=200"))
    page.get_by_role("button", name=re.compile(r"Show more")).click()
    expect(rows).to_have_count(every)
    assert all("500" in x for x in page.problems), page.problems
    page.problems.clear()
    clean(page)


def test_the_hub_filters_by_kind(page, world):
    hub_files(world, 3)
    one_file(world, RECEIPT, "photo.png", "pic", "image/png")
    docs(page)
    expect(page.locator("#docList .doc-fn").first).to_be_visible()
    page.locator("#docFilters button", has_text="Images").click()
    expect(page.locator("#docList .doc-fn")).to_have_text(["photo.png"])
    page.locator("#docFilters button", has_text="All").click()
    expect(page.locator("#docList .doc-fn").filter(has_text="report-")).to_have_count(3)
    clean(page)


def test_a_file_opens_its_mail_and_downloads_from_its_link(page, world):
    one_file(world, RECEIPT, "invoice.pdf")
    docs(page)
    row = page.locator(".doc-row", has_text="invoice.pdf")
    link = row.get_by_role("link", name="Download invoice.pdf")
    expect(link).to_have_attribute("download", "invoice.pdf")
    row.get_by_role("button").click()
    expect(page.get_by_role("dialog", name="Documents")).to_have_count(0)
    expect(page.locator(".rd-subj")).to_have_text("Your receipt for order 1042")
    clean(page)


def test_opening_the_hub_again_reads_it_again(page, world):
    hub_files(world, 1)
    docs(page)
    expect(page.locator("#docList .doc-fn").filter(has_text="report-")).to_have_count(1)
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog", name="Documents")).to_have_count(0)
    one_file(world, RECEIPT, "later.pdf")
    page.keyboard.press("ControlOrMeta+k")
    page.get_by_role("combobox", name="Command").fill("Documents")
    page.keyboard.press("Enter")
    expect(page.locator("#docList .doc-fn").filter(has_text="later.pdf")).to_have_count(1)
    clean(page)


def test_the_hub_passes_axe(page, world, every_theme):
    hub_files(world, 3)
    docs(page)
    expect(page.locator("#docList .doc-row").first).to_be_visible()
    page.pw.evaluate(FADES_DONE)
    found = Axe().run(page.pw, options={"iframes": False}).response["violations"]
    assert found == [], [(v["id"], [(n["html"][:70], n["any"][0]["message"][:170] if n["any"] else "") for n in v["nodes"]]) for v in found]
    clean(page)
