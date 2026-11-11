"""The floats, on the fictional world: the toast, the offer of Undo and a send on its way out, what each says, how long for, and
who hears it. One primitive shows them all (`Float` in web/src/lib/toast.tsx); the composer's own rules for a send are in
tests/e2e/browser/test_compose.py and web/src/features/compose/machine.test.ts."""
import re

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import expect

pytestmark = pytest.mark.browser

RECEIPT = "ada-inbox-05-receipt.eml"
LIN = "lin@example.test"


def clean(page):
    assert page.problems == [], page.problems
    assert page.blocked == [], page.blocked


def until(page, check, what):
    for _ in range(100):
        if check():
            return
        page.wait_for_timeout(50)
    assert check(), what


def opened(page):
    page.goto("/")
    page.get_by_role("tab", name=re.compile("^Everything")).click()
    expect(page.get_by_role("option").first).to_be_visible()
    return page


def row(page, world, name):
    assert world.pk(name) is not None, f"{name} has no row in the database"
    return page.locator('#rows .row[data-key="{key}"][data-folder="{folder}"]'.format(**world.copy(name)))


def bar_of(float_):
    return float_.locator(".float-bar")


def lasts(bar):
    return bar.evaluate("e => getComputedStyle(e).animationDuration")


def writing(page, to, subject, text):
    page.keyboard.press("c")
    page.get_by_role("combobox", name="To").fill(to)
    page.get_by_role("textbox", name="subject").fill(subject)
    page.get_by_role("textbox", name="message body").fill(text)


def send(page):
    page.locator("#cmpSend").click()


# ── how long, and how you can tell ─────────────────────────────────────

def test_the_offer_of_undo_shows_its_time_running_out_and_a_new_offer_starts_it_over(page, world):
    opened(page)
    row(page, world, RECEIPT).click()
    page.keyboard.press("e")
    float_ = page.locator("#undoFloat")
    expect(float_).to_contain_text("Archived")
    bar = bar_of(float_)
    expect(bar).to_be_visible()
    assert lasts(bar) == "8s"                                  # the offer's own life
    expect(page.locator("#toast .float-bar")).to_have_count(0)   # a toast offers nothing, so it has nothing to run out of
    first = bar.element_handle()
    page.keyboard.press("e")                                   # a second filing makes a second offer
    expect(page.locator("#toast")).to_have_text(re.compile("Archived"))
    until(page, lambda: first.evaluate("e => !e.isConnected"), "a new offer kept the old bar")
    assert lasts(bar_of(page.locator("#undoFloat"))) == "8s"
    page.clock.run_for(8_100)
    expect(page.locator("#undoFloat")).to_have_count(0)
    clean(page)


def test_a_sends_countdown_drains_once_over_the_window_and_doesnt_start_over_each_second(page, world):
    opened(page)
    writing(page, LIN, "Fabric order", "Hi Lin, the linen came in.")
    send(page)
    float_ = page.locator("#sendFloat")
    expect(float_).to_contain_text("Sending in 10s")
    bar = bar_of(float_)
    expect(bar).to_be_visible()
    assert lasts(bar) == "10s"                                 # the window Undo Send has
    same = bar.element_handle()
    page.clock.run_for(3_000)
    expect(float_).to_contain_text("Sending in 7s")
    assert same.evaluate("e => e.isConnected")                  # the same bar, still running
    page.clock.run_for(8_000)
    expect(float_).to_contain_text("Sent")
    expect(bar_of(float_)).to_have_count(0)                     # nothing left to run out of
    clean(page)


# ── who hears it ────────────────────────────────────────────────────────

def live_of(page, inner):
    return page.get_by_role("status").filter(has=page.locator(inner))


def test_the_send_and_undo_floats_are_read_out_and_the_ticking_number_is_not(page, world):
    opened(page)
    expect(page.locator(".float-live")).to_have_count(2)          # already there, so what lands in them is read
    row(page, world, RECEIPT).click()
    page.keyboard.press("e")
    expect(page.locator("#undoFloat")).to_contain_text("Archived")
    offer = live_of(page, "#undoFloat")
    expect(offer).to_contain_text("Undo is available")
    expect(offer.locator("#ufUndo")).to_contain_text("Undo")

    writing(page, LIN, "Fabric order", "Hi Lin, the linen came in.")
    send(page)
    float_ = page.locator("#sendFloat")
    expect(float_).to_contain_text("Sending in 10s")
    status = live_of(page, "#sendFloat")
    expect(status).to_contain_text("You have 10 seconds to undo")
    expect(float_.locator('[aria-hidden="true"]', has_text="Sending in 10s")).to_have_count(1)   # the tick is for the eye
    page.clock.run_for(3_000)
    expect(float_).to_contain_text("Sending in 7s")
    expect(status).to_contain_text("You have 10 seconds to undo")   # no new words: it isn't read out again
    page.clock.run_for(8_000)
    expect(float_).to_contain_text("Sent")
    expect(status).to_contain_text("Sent · 1 recipient")
    clean(page)


# ── what axe finds on them, and on a row in each of its states, in every theme ─

# Axe judges contrast as it finds it, so fades must end first. Not the bar, which is made to run out.
FADES_DONE = """Promise.all(document.getAnimations()
    .filter(a => a.animationName !== 'floatDrain' && a.effect.getComputedTiming().iterations !== Infinity)
    .map(a => a.finished.catch(() => null)))"""


def axe_says(page):
    page.pw.evaluate(FADES_DONE)
    found = Axe().run(page.pw, options={"iframes": False}).response["violations"]
    return {v["id"]: [n["target"] for n in v["nodes"]] for v in found}


def axe_says_held(page):
    """What axe says with the page's clock held, so a float that goes by itself stays while axe looks at it. axe's own steps
    wait on that clock's timers, so it is given time on it a frame at a time until it has done, far less than a float lasts."""
    page.pw.evaluate(FADES_DONE)
    page.pw.evaluate(Axe().axe_script)
    page.pw.evaluate("""() => { window.axeSaid = null;
        axe.run({iframes: false}).then(r => { window.axeSaid = r.violations.map(v => [v.id, v.nodes.map(n => n.target)]) }) }""")
    for _ in range(60):
        if page.pw.evaluate("window.axeSaid !== null"):
            break
        page.clock.run_for(16)
    said = page.pw.evaluate("window.axeSaid")
    assert said is not None, "axe hadn't done after a second of the page's time"
    return dict(said)


def test_axe_finds_nothing_on_a_rows_states_or_the_floats(page, world, every_theme):
    opened(page)
    rows = page.locator("#rows .row.cmsg")
    rows.filter(has=page.locator(".jb")).first.click()         # the chip, on a selected row
    expect(page.locator("#rows .row.sel .jb")).to_be_visible()
    rows.first.locator(".cbx").click(force=True)               # a marked row beside it
    rows.nth(1).hover()
    assert axe_says(page) == {}

    page.keyboard.press("e")
    expect(page.locator("#undoFloat")).to_contain_text("Archived")
    assert axe_says(page) == {}

    writing(page, LIN, "Fabric order", "Hi Lin, the linen came in.")
    send(page)
    float_ = page.locator("#sendFloat")
    expect(float_).to_contain_text("Sending in")
    # Held from here, so the countdown waits for each check: "Sent" stays up for 2.4 s of the page's clock, and a busy
    # machine can spend longer than that on axe.
    page.clock.pause_at(page.evaluate("Date.now()") + 1_000)
    assert axe_says_held(page) == {}
    page.clock.run_for(int(re.search(r"Sending in (\d+)s", float_.inner_text())[1]) * 1_000)
    expect(float_).to_contain_text("Sent")
    assert axe_says_held(page) == {}
    clean(page)
