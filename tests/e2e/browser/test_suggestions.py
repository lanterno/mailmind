"""What a message proposes, found on a click and shown as suggestions: confirmed or edited in the reader, with nothing made until
someone says so, and the tasks listed where Commitments are."""
import pytest
from playwright.sync_api import expect

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.bootstrap.mailbox import store
from mailmind.bootstrap import mailbox
from mailmind.bootstrap import accounts

pytestmark = pytest.mark.browser

RECEIPT = "ada-inbox-05-receipt.eml"
TITLE = "Review the invoice"
EVENT = {"title": "Quarterly review", "start": "2026-11-12T15:00", "end": "2026-11-12T16:00", "place": "Room 2"}


def clean(page):
    assert page.problems == [], page.problems
    assert page.blocked == [], page.blocked


def tasks_stored():
    c = store.conn()
    try:
        return [dict(r) for r in c.execute("SELECT * FROM tasks")]
    finally:
        c.close()


def opening_the_message(page, world):
    page.goto("/")
    page.get_by_role("tab", name="Everything").click()
    row = page.locator('#rows .row[data-key="{key}"][data-folder="{folder}"]'.format(**world.copy(RECEIPT)))
    expect(row).to_be_visible()
    row.click()
    expect(page.locator('[data-ca="extract"]')).to_be_visible()


def found(ai, events=(), tasks=()):
    ai.queue(fa.answer({"events": list(events), "tasks": list(tasks)}))


def test_nothing_is_made_until_a_task_is_confirmed_and_then_it_is_listed(page, world, ai):
    found(ai, tasks=[{"title": TITLE, "due": "2026-11-09"}])
    opening_the_message(page, world)
    page.locator('[data-ca="extract"]').click()
    form = page.locator('[data-suggestion="task"]')
    expect(form).to_be_visible()
    assert tasks_stored() == []
    form.get_by_label("Title").fill("Pay the invoice")
    form.get_by_label("Due").fill("2026-11-15")
    form.get_by_role("button", name="Add to tasks").click()
    expect(form).to_have_count(0)
    stored = tasks_stored()
    assert [(t["title"], t["due"]) for t in stored] == [("Pay the invoice", "2026-11-15")]
    assert stored[0]["message_key"] == world.key(RECEIPT)
    page.keyboard.press("ControlOrMeta+k")
    page.get_by_role("option", name="Commitments").first.click()
    row = page.locator(f'#taskList [data-task="{stored[0]["id"]}"]')
    expect(row.locator(".cm-action")).to_have_text("Pay the invoice")
    expect(row.locator(".due")).to_have_text("Sun, Nov 15")
    clean(page)


def test_a_task_is_marked_done_and_removed_in_the_list(page, world, ai):
    found(ai, tasks=[{"title": TITLE, "due": ""}])
    opening_the_message(page, world)
    page.locator('[data-ca="extract"]').click()
    page.locator('[data-suggestion="task"]').get_by_role("button", name="Add to tasks").click()
    expect(page.locator('[data-suggestion="task"]')).to_have_count(0)
    tid = tasks_stored()[0]["id"]
    page.keyboard.press("ControlOrMeta+k")
    page.get_by_role("option", name="Commitments").first.click()
    row = page.locator(f'#taskList [data-task="{tid}"]')
    row.get_by_role("button", name="Mark done").click()
    expect(row).to_have_class("commit-row task done")
    assert tasks_stored()[0]["done_at"] is not None
    row.get_by_role("button", name="Remove task").click()
    expect(page.locator("#taskList")).to_have_count(0)
    assert tasks_stored() == []
    clean(page)


def test_a_task_links_back_to_its_message(page, world, ai):
    found(ai, tasks=[{"title": TITLE, "due": ""}])
    opening_the_message(page, world)
    page.locator('[data-ca="extract"]').click()
    page.locator('[data-suggestion="task"]').get_by_role("button", name="Add to tasks").click()
    expect(page.locator('[data-suggestion="task"]')).to_have_count(0)
    page.keyboard.press("ControlOrMeta+k")
    page.get_by_role("option", name="Commitments").first.click()
    page.locator("#taskList .cm-main").click()
    expect(page.locator(".commit-overlay")).to_have_count(0)
    expect(page.locator(".rd-subj")).to_contain_text(world.row(RECEIPT)["subject"])
    clean(page)


def test_an_event_confirmed_gives_a_file_and_is_edited_first(page, world, ai):
    found(ai, events=[EVENT])
    opening_the_message(page, world)
    page.locator('[data-ca="extract"]').click()
    form = page.locator('[data-suggestion="event"]')
    expect(form.get_by_label("Starts")).to_have_value("2026-11-12T15:00")
    form.get_by_label("Title").fill("Quarterly review, edited")
    with page.expect_download() as got:
        form.get_by_role("button", name="Add to calendar").click()
    assert got.value.suggested_filename == "event.ics"
    with open(got.value.path(), encoding="utf-8", newline="") as f:
        text = f.read()
    assert "METHOD:PUBLISH" in text and "ATTENDEE" not in text
    assert "SUMMARY:Quarterly review\\, edited" in text
    assert "DTSTART:20261112T150000Z" in text and "LOCATION:Room 2" in text
    expect(form).to_have_count(0)
    clean(page)


def test_a_date_that_wont_do_is_refused_and_the_form_stays(page, world, ai):
    found(ai, events=[EVENT])
    opening_the_message(page, world)
    page.locator('[data-ca="extract"]').click()
    form = page.locator('[data-suggestion="event"]')
    form.get_by_label("Ends").fill("2026-11-12T14:00")
    form.get_by_role("button", name="Add to calendar").click()
    expect(form.locator(".sg-refused")).to_have_text("that end doesn’t come after the start")
    expect(form).to_be_visible()
    assert all("status of 400" in p for p in page.problems), page.problems
    page.problems.clear()
    clean(page)


def test_dismissing_keeps_nothing(page, world, ai):
    found(ai, tasks=[{"title": TITLE, "due": ""}])
    opening_the_message(page, world)
    page.locator('[data-ca="extract"]').click()
    page.locator('[data-suggestion="task"]').get_by_role("button", name="Dismiss").click()
    expect(page.locator("#sgNone")).to_have_text("That’s everything it found.")
    assert tasks_stored() == []
    clean(page)


def test_nothing_found_says_so(page, world, ai):
    found(ai)
    opening_the_message(page, world)
    page.locator('[data-ca="extract"]').click()
    expect(page.locator("#sgNone")).to_have_text("Nothing here proposes a meeting or a dated task.")
    clean(page)


def test_ai_off_says_so(page, world):
    opening_the_message(page, world)
    page.locator('[data-ca="extract"]').click()
    expect(page.locator("#sgCard .ai-err")).to_contain_text("AI is off")
    assert all("status of 409" in p for p in page.problems), page.problems
    page.problems.clear()
    clean(page)


def test_a_renumber_leaves_a_tasks_link(page, world, ai):
    found(ai, tasks=[{"title": TITLE, "due": ""}])
    opening_the_message(page, world)
    page.locator('[data-ca="extract"]').click()
    page.locator('[data-suggestion="task"]').get_by_role("button", name="Add to tasks").click()
    expect(page.locator('[data-suggestion="task"]')).to_have_count(0)
    world.server.renumber(w.ADA, "INBOX")
    mailbox.ingest.sync_new(accounts.get(w.ADA))
    page.reload()
    expect(page.locator(".rd-subj")).to_be_visible()
    page.keyboard.press("ControlOrMeta+k")
    page.get_by_role("option", name="Commitments").first.click()
    expect(page.locator("#taskList .cm-action")).to_have_text(TITLE)
    expect(page.locator("#taskList .cm-main")).to_be_enabled()
    clean(page)


def test_the_card_has_nothing_axe_finds(page, world, ai, every_theme):
    found(ai, events=[EVENT], tasks=[{"title": TITLE, "due": "2026-11-09"}])
    opening_the_message(page, world)
    page.locator('[data-ca="extract"]').click()
    expect(page.locator('[data-suggestion="task"]')).to_be_visible()
    from axe_playwright_python.sync_playwright import Axe
    assert [v["id"] for v in Axe().run(page.pw).response["violations"]] == []
    clean(page)
