"""The Screener and the contact card: deciding on a sender, and who a sender is."""
import re

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import expect

from fakes import anthropic_api as fa
from fixtures import world as w

pytestmark = pytest.mark.browser

RECEIPT = "ada-inbox-05-receipt.eml"
THIMBLE = "orders@thimble.example.test"


# Axe judges contrast as it finds it, so fade-ins must end first. A looping animation never ends: waiting for one hangs
# for good, since evaluate has no timeout.
FADES_DONE = """Promise.all(document.getAnimations()
    .filter(a => a.effect.getComputedTiming().iterations !== Infinity)
    .map(a => a.finished.catch(() => null)))"""


def clean(page):
    assert page.problems == [], page.problems
    assert page.blocked == [], page.blocked


def until(page, check, what):
    for _ in range(100):
        if check():
            return
        page.wait_for_timeout(50)
    assert check(), what


def screener(page):
    page.goto(f"/m/{w.ADA}/INBOX")
    page.locator('a.frow[data-folder="__screener__"]').click()
    expect(page.locator(".scr-row").first).to_be_visible()
    return page


def rules_now():
    from mailmind.bootstrap.curation import rules
    return rules.load()


def test_the_rail_counts_senders_to_screen_and_the_list_names_them(page, world):
    screener(page)
    n = page.locator(".scr-row").count()
    expect(page.locator('a.frow[data-folder="__screener__"] .unread')).to_have_text(str(n))
    expect(page.locator("#listTitle")).to_have_text("Screener")
    expect(page.locator("#count")).to_have_text(f"{n} to screen")
    expect(page.locator(f'.scr-row[data-scr="{THIMBLE}"] .scr-ad')).to_contain_text(THIMBLE)
    clean(page)


def test_the_ai_suggests_on_a_click_and_only_a_click_on_a_row_decides(page, world, ai):
    screener(page)
    assert ai.requests == []
    before = rules_now()
    first = page.locator(".scr-row").first
    addr = first.get_attribute("data-scr")
    page.get_by_role("button", name=re.compile("Suggest")).click()
    expect(first.locator(".scr-hint")).to_contain_text("Allow? stub why")
    expect(first.locator(".scr-allow")).to_have_class(re.compile("suggested"))
    assert (rules_now(), len(ai.requests)) == (before, 1)
    expect(page.locator(".scr-bar")).to_contain_text("for 1 of these")
    first.locator(".scr-allow").click()
    expect(page.locator(f'.scr-row[data-scr="{addr}"]')).to_have_count(0)
    until(page, lambda: addr in rules_now()["keep_senders"], "the click wrote the rule")
    clean(page)


def test_allowing_a_sender_keeps_them_and_takes_them_off_the_list(page, world):
    screener(page)
    n = page.locator(".scr-row").count()
    page.locator(f'.scr-row[data-scr="{THIMBLE}"]').get_by_role("button", name="Allow").click()
    expect(page.locator(f'.scr-row[data-scr="{THIMBLE}"]')).to_have_count(0)
    expect(page.locator("#toast")).to_contain_text("Allowed orders")
    assert THIMBLE in rules_now()["keep_senders"]
    expect(page.locator("#count")).to_have_text(f"{n - 1} to screen")
    expect(page.locator('a.frow[data-folder="__screener__"] .unread')).to_have_text(str(n - 1))
    clean(page)


def test_screening_out_blocks_the_sender(page, world):
    screener(page)
    page.locator(f'.scr-row[data-scr="{THIMBLE}"]').get_by_role("button", name="Screen out").click()
    expect(page.locator(f'.scr-row[data-scr="{THIMBLE}"]')).to_have_count(0)
    expect(page.locator("#toast")).to_contain_text("Screened out orders")
    assert THIMBLE in rules_now()["block_senders"]
    clean(page)


def test_a_refused_decision_brings_the_sender_back(page, world):
    screener(page)
    page.pw.route("**/api/rules", lambda route: route.fulfill(status=500, body="{}") if route.request.method == "POST" else route.continue_())
    page.locator(f'.scr-row[data-scr="{THIMBLE}"]').get_by_role("button", name="Allow").click()
    expect(page.locator("#toast")).to_have_text("Couldn’t apply")
    expect(page.locator(f'.scr-row[data-scr="{THIMBLE}"]')).to_have_count(1)
    assert all("500" in x for x in page.problems), page.problems
    page.problems.clear()
    clean(page)


def test_the_last_decision_leaves_the_all_clear(page, world):
    page.pw.route(re.compile(r"/api/screener\?"), lambda route: route.fulfill(json={"ok": True, "account": w.ADA, "items": [], "total": 0}))
    page.goto(f"/m/{w.ADA}/__screener__")
    expect(page.locator(".ac-h")).to_have_text("Screener clear")
    expect(page.locator(".ac-b")).to_have_text("Every sender in this inbox has been allowed or screened out.")
    expect(page.locator('a.frow[data-folder="__screener__"]')).to_have_count(0)
    clean(page)


def opened_receipt(page, world):
    page.goto(f"/m/{w.ADA}/INBOX?key={world.key(RECEIPT)}&folder=INBOX&uid={world.uid(RECEIPT)}")
    expect(page.locator(".rd-subj")).to_have_text("Your receipt for order 1042")
    return page


def test_summarizing_a_sender_waits_for_the_click_and_shows_what_came_back(page, world, ai):
    opened_receipt(page, world)
    page.locator("button.rd-sender").click()
    card = page.get_by_role("dialog", name="About this sender")
    button = card.get_by_role("button", name="Summarize this sender")
    expect(button).to_be_visible()
    asked = lambda: [r for r in ai.requests if "what one sender has been writing" in r.prompt]
    assert asked() == []
    ai.queue(fa.answer({"summary": "A shop you buy thread from.", "topics": ["orders"], "open": "Confirm the address."}))
    button.click()
    expect(card.locator(".cc-sum p").first).to_have_text("A shop you buy thread from.")
    expect(card.locator(".cc-topics")).to_have_text("orders")
    expect(card.locator(".cc-open")).to_contain_text("Confirm the address.")
    expect(button).to_have_count(0)
    assert len(asked()) == 1
    clean(page)


def test_with_ai_off_a_sender_summary_says_so(page, world):
    opened_receipt(page, world)
    page.locator("button.rd-sender").click()
    card = page.get_by_role("dialog", name="About this sender")
    card.get_by_role("button", name="Summarize this sender").click()
    expect(card.locator(".cc-sum")).to_contain_text("AI is off.")
    expect(card.get_by_role("button", name="Summarize this sender")).to_have_count(0)


def test_the_sender_opens_a_card_that_says_who_they_are(page, world):
    opened_receipt(page, world)
    sender = page.locator("button.rd-sender")
    sender.click()
    card = page.get_by_role("dialog", name="About this sender")
    expect(card.locator(".cc-name")).to_have_text("Thimble & Co")
    expect(card.locator(".cc-addr")).to_have_text(THIMBLE)
    expect(card.locator(".cc-stats")).to_contain_text("email")
    expect(card.get_by_role("button", name="Compose")).to_be_visible()
    page.keyboard.press("Escape")
    expect(card).to_have_count(0)
    expect(sender).to_be_focused()
    expect(page.locator(".rd-subj")).to_be_visible()
    clean(page)


def test_a_thread_on_the_card_opens_it_and_the_card_closes(page, world):
    world.deliver(w.ADA, "INBOX", w.variant(RECEIPT, Message_ID="<order-1043@thimble.example.test>", Subject="Your next receipt", Date="Fri, 30 Oct 2026 11:05:00 +0000"))
    opened_receipt(page, world)
    page.locator("button.rd-sender").click()
    page.locator(".cc-thread", has_text="Your next receipt").click()
    expect(page.locator(".contact-card")).to_have_count(0)
    expect(page.locator(".rd-subj")).to_have_text(re.compile(r"^Your next receipt\s+1 message$"))
    clean(page)


def test_from_this_sender_searches_for_them(page, world):
    opened_receipt(page, world)
    page.locator("button.rd-sender").click()
    page.get_by_role("button", name="From this sender").click()
    page.expect_where(r"/search\?q=from%3A")
    expect(page.get_by_role("option").first).to_contain_text("Thimble")
    clean(page)


def test_compose_from_the_card_starts_a_message_to_them(page, world):
    opened_receipt(page, world)
    page.locator("button.rd-sender").click()
    page.get_by_role("dialog", name="About this sender").get_by_role("button", name="Compose").click()
    expect(page.locator("#cmpTo .cm-chip").first).to_contain_text(THIMBLE)
    clean(page)


def test_a_card_that_cannot_load_says_so(page, world):
    page.pw.route(re.compile(r"/api/contact\?"), lambda route: route.fulfill(status=500, body="{}"))
    opened_receipt(page, world)
    page.locator("button.rd-sender").click()
    expect(page.locator(".cc-load")).to_have_text("Couldn’t load this sender.")
    assert all("500" in x for x in page.problems), page.problems
    page.problems.clear()
    clean(page)


def with_headers(subject, extra):
    """One more receipt from the same sender, with these header lines added."""
    raw = w.variant(RECEIPT, Message_ID=f"<{subject.replace(' ', '-')}@thimble.example.test>", Subject=subject, Date="Fri, 30 Oct 2026 11:05:00 +0000")
    head, sep, body = raw.partition(b"\n\n")
    return head + b"".join(f"\n{line}".encode() for line in extra) + sep + body


def opened_mail(page, world, subject, reply_to):
    world.deliver(w.ADA, "INBOX", with_headers(subject, [f"Reply-To: {reply_to}"]))
    page.goto(f"/m/{w.ADA}/INBOX")
    page.locator("#tab-all").click()
    page.locator("#rows .row[data-key]").filter(has_text=subject).click()
    expect(page.locator(".rd-subj")).to_contain_text(subject)
    return page


def test_the_reader_says_who_a_sender_is_without_asking_again(page, world):
    asked = []
    page.pw.on("request", lambda r: asked.append(r.url) if "/api/contact?" in r.url else None)
    opened_receipt(page, world)
    expect(page.locator(".rd-from .rd-mono")).to_have_text("T")
    expect(page.locator(".rd-from .rd-facts")).to_have_text(re.compile(r"^\d+ emails? · replied \d+× · first .+ ago$"))
    assert asked == []
    clean(page)


def test_a_reply_to_at_another_organisation_warns_outside_the_mail(page, world):
    opened_mail(page, world, "Wire the deposit", reply_to="Accounts <accounts@elsewhere.test>")
    note = page.locator(".rd-replyto")
    expect(note).to_have_text("Replies go to accounts@elsewhere.test, not to the sender, orders@thimble.example.test.")
    expect(page.frame_locator(".rd-iframe").locator("body")).not_to_contain_text("Replies go to")
    clean(page)


def test_a_reply_to_inside_the_senders_organisation_says_nothing(page, world):
    opened_mail(page, world, "Your delivery", reply_to="help@support.thimble.example.test")
    expect(page.locator(".rd-iframe")).to_be_visible()
    expect(page.locator(".rd-replyto")).to_have_count(0)
    clean(page)


def test_a_conversation_warns_of_a_reply_to_too(page, world):
    first = "<order-1042@thimble.example.test>"
    world.deliver(w.ADA, "INBOX", with_headers("Re: Your receipt for order 1042", ["Reply-To: accounts@elsewhere.test", f"In-Reply-To: {first}", f"References: {first}"]))
    page.goto(f"/m/{w.ADA}/INBOX")
    page.locator("#tab-all").click()
    page.locator("#rows .row[data-key]").filter(has=page.locator(".thr-count")).filter(has_text="Your receipt").click()
    expect(page.locator(".tmsg.open .rd-replyto")).to_contain_text("accounts@elsewhere.test")
    clean(page)


def test_the_screener_and_the_card_pass_axe(page, world, every_theme):
    found = lambda: [(v["id"], [n["html"][:90] for n in v["nodes"]]) for v in Axe().run(page.pw, options={"iframes": False}).response["violations"]]
    screener(page)
    page.pw.evaluate(FADES_DONE)
    assert found() == []
    opened_receipt(page, world)
    page.locator("button.rd-sender").click()
    expect(page.locator(".cc-name")).to_be_visible()
    page.pw.evaluate(FADES_DONE)
    assert found() == []
    page.keyboard.press("Escape")
    expect(page.locator(".contact-card")).to_have_count(0)
    opened_mail(page, world, "Wire the deposit", reply_to="accounts@elsewhere.test")
    expect(page.locator(".rd-replyto")).to_be_visible()
    page.pw.evaluate(FADES_DONE)
    assert found() == []
    clean(page)
