"""The status pill, the help overlay and Settings."""
import json
import re
import time

import pytest
from playwright.sync_api import expect

from fixtures import world as w
from mailmind.bootstrap import mailbox

pytestmark = pytest.mark.browser


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


def test_the_sync_menu_says_what_ai_cost_today_and_sync_now_syncs(page, world, ai):
    from mailmind.assistant.application.gateway import local_day
    from mailmind.assistant.application.ports import Spend, Usage
    from mailmind.bootstrap import assistant
    now = time.time()
    assistant.LEDGER.record(Spend(local_day(now), "triage", "claude-opus-5-5", "claude-opus-5-5", "ok",
                                  Usage(input_tokens=3000, output_tokens=200), 16_000, int(now)))
    opened(page)
    page.locator("#statusBtn").click()
    menu = page.get_by_role("dialog", name="Sync status")
    expect(menu).to_contain_text("Claude API · claude-opus-5-5 · $0.02 of $15.00 today")
    menu.get_by_role("button", name="Sync now").click()
    expect(page.locator("#statusTxt")).to_have_text("Syncing…")
    page.clock.run_for(4000)
    expect(page.locator("#toast")).to_contain_text("Sync complete")
    expect(page.locator("#statusTxt")).to_have_text(re.compile("^(Synced|Not synced)"))
    clean(page)


def test_reconcile_says_when_theres_nothing_to_do(page, world):
    opened(page)
    page.locator("#statusBtn").click()
    page.get_by_role("dialog", name="Sync status").get_by_role("button", name=re.compile("^Reconcile")).click()
    expect(page.locator("#toast")).to_contain_text("Nothing to reconcile")
    clean(page)


def test_an_account_that_cant_connect_says_so_and_settings_trusts_a_new_certificate(page, world):
    from mailmind.bootstrap.accounts import config
    where = f"127.0.0.1:{world.server.port}"

    def bea(raw):
        return next(a for a in raw["accounts"] if a["email"] == w.BEA)
    raw = config._raw()
    bea(raw)["tls_pins"] = {where: "sha256:" + "0" * 64}
    config.save(raw)                                      # as if Bridge had been reinstalled
    mailbox.syncing.sync_accounts({w.BEA})
    opened(page)
    page.locator("#settingsBtn").click()
    row = page.locator(f'.set-row[data-email="{w.BEA}"]')
    expect(row).to_contain_text("certificate changed")
    expect(row).to_contain_text("MailMind sent no password")
    row.get_by_role("button", name="Trust it").click()
    expect(row).to_contain_text("✓ synced", timeout=10_000)
    assert bea(config._raw())["tls_pins"] == {where: "sha256:" + world.server.cert_sha256}
    clean(page)


def test_settings_adds_edits_tests_and_removes_a_connection(page, world):
    opened(page)
    page.locator("#settingsBtn").click()
    dialog = page.get_by_role("dialog", name="Email connections")
    expect(dialog.locator(".set-row")).to_have_count(3)
    expect(dialog.locator("#exportPlan")).to_contain_text("messages")
    assert w.ADA in dialog.locator(".set-row").first.get_attribute("data-email")

    page.get_by_role("button", name="+ Add a connection").click()
    expect(dialog.get_by_label("Alias (optional)")).to_be_focused()
    dialog.get_by_role("button", name="Save connection").click()
    expect(dialog.locator(".err")).to_have_text("A valid email is required.")
    dialog.get_by_label("Email", exact=True).fill("dee@example.test")
    dialog.get_by_role("button", name="Save connection").click()
    expect(dialog.locator(".err")).to_have_text("Set a password (or 1Password ref / env var).")
    dialog.get_by_role("button", name="IMAP", exact=True).click()
    dialog.get_by_role("button", name="Save connection").click()
    expect(dialog.locator(".err")).to_have_text("A host is required for a generic IMAP account.")
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog.locator(".set-row")).to_have_count(3)

    row = dialog.locator(f'.set-row[data-email="{w.BEA}"]')
    row.get_by_role("button", name="Edit").click()
    dialog.get_by_label("Alias (optional)").fill("Bea at work")
    dialog.get_by_role("button", name="Save connection").click()
    expect(row.locator(".nm")).to_have_text("Bea at work")

    row.get_by_role("button", name="Test").click()
    expect(row.locator(".set-status")).to_have_text(re.compile("Connected|✕"))

    row.get_by_role("button", name="Remove").click()
    expect(row.get_by_role("button", name="Confirm?")).to_be_visible()
    row.get_by_role("button", name="Confirm?").click()
    expect(row).to_have_count(0)
    clean(page)


def test_the_help_overlay_lists_the_keys_and_can_turn_single_keys_off(page, world):
    opened(page)
    page.keyboard.press("?")
    dialog = page.get_by_role("dialog", name="Keyboard")
    expect(dialog).to_contain_text("Next unread")
    dialog.get_by_label(re.compile("Disable single-key shortcuts")).check()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    page.keyboard.press("c")                           # the setting took
    expect(page.locator("#cmpBody")).to_have_count(0)
    page.reload()
    expect(page.get_by_role("option").first).to_be_visible()
    page.keyboard.press("c")
    expect(page.locator("#cmpBody")).to_have_count(0)
    page.keyboard.press("?")                           # how you find the setting again, as on the page
    dialog.get_by_label(re.compile("Disable single-key shortcuts")).uncheck()
    clean(page)


LEGEND_ROWS = """gs => gs.flatMap(g => {
    const cells = [...g.children], rows = [];
    for (let i = 0; i < cells.length; i += 2) rows.push([[...cells[i].querySelectorAll('kbd')].map(k => k.textContent), cells[i + 1].textContent.trim()]);
    return rows;
})"""


def test_the_legend_comes_from_the_commands_and_names_keys_nothing_open_would_offer(page, world):
    opened(page)                                                 # the Inbox: no message open, nothing marked
    page.keyboard.press("?")
    dialog = page.get_by_role("dialog", name="Keyboard")
    expect(dialog).to_be_visible()
    keys = {words: k for k, words in dialog.locator(".g").evaluate_all(LEGEND_ROWS)}
    assert keys["Reply later"] == ["l"]                          # the key it never had
    assert keys["Archive"] == ["e"] and keys["Reply"] == ["r"]   # these need a message open
    assert keys["Delete"] == ["d", "#", "⌦", "⌫"]                 # every key the command takes
    assert keys["Next message"] == ["j", "↓"]
    assert keys["Save this search as a view"] == ["v"]           # only a search offers it
    assert keys["Commands"] == ["⌘K"]                            # no command owns this one
    go_to = next(words for k, words in dialog.locator(".g").evaluate_all(LEGEND_ROWS) if k[0] == "g")
    assert go_to.startswith("Go to folder") and "l reply later" in go_to and "i inbox" in go_to and "w waiting" in go_to
    page.keyboard.press("Escape")
    page.keyboard.press("g")
    expect(page.locator("#toast")).to_have_text(re.compile(r"Go to folder:.*i·.*·l$"))
    page.keyboard.press("Escape")
    clean(page)


def test_axe_finds_nothing_on_status_help_or_settings(page, world, every_theme):
    from axe_playwright_python.sync_playwright import Axe
    found = lambda: {v["id"]: [n["target"] for n in v["nodes"]] for v in Axe().run(page.pw, options={"iframes": False}).response["violations"]}
    opened(page)
    page.locator("#statusBtn").click()
    expect(page.get_by_role("dialog", name="Sync status")).to_be_visible()
    assert found() == {}
    page.keyboard.press("Escape")
    page.locator("#helpBtn").click()
    expect(page.get_by_role("dialog", name="Keyboard")).to_be_visible()
    assert found() == {}
    page.keyboard.press("Escape")
    page.locator("#settingsBtn").click()
    expect(page.locator(".set-row").first).to_be_visible()
    assert found() == {}
    page.get_by_role("button", name="+ Add a connection").click()
    assert found() == {}
    page.get_by_role("button", name="Cancel").click()
    page.get_by_role("tab", name="Preferences").click()
    expect(page.locator("#prefsBody")).to_be_visible()
    assert found() == {}
    clean(page)


def test_with_no_accounts_the_welcome_offers_settings(page, world):
    page.pw.route(re.compile(r"/api/digest"), lambda route: route.fulfill(json={**route.fetch().json(), "accounts": [], "send_accounts": []}))
    page.goto("/")
    expect(page.locator(".firstrun .fr-h")).to_have_text("Welcome to MailMind")
    page.locator("#frAdd").click()
    expect(page.locator("#setClose")).to_be_visible()
    clean(page)


# ── the Settings home and its Preferences ───────────────────────────────────────────────────────────────────────────


def file_of(world):
    return json.loads((world.dir / "mailmind.config.json").read_text())


def preferences_open(page):
    opened(page)
    page.locator("#settingsBtn").click()
    page.get_by_role("tab", name="Preferences").click()
    dialog = page.get_by_role("dialog", name="Preferences")
    expect(dialog).to_be_visible()
    return dialog


def test_settings_has_a_preferences_tab_beside_connections_that_steps_back_on_esc(page, world):
    opened(page)
    page.locator("#settingsBtn").click()
    dialog = page.get_by_role("dialog", name="Email connections")
    expect(dialog.locator(".set-row")).to_have_count(3)
    page.get_by_role("tab", name="Preferences").click()
    expect(page.get_by_role("dialog", name="Preferences")).to_be_visible()
    expect(page.locator("#prefsBody")).to_contain_text("Theme")
    page.get_by_role("tab", name="Connections").click()
    expect(dialog.locator(".set-row")).to_have_count(3)
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    clean(page)


def test_a_preference_is_saved_to_the_file_and_outlives_a_browser_data_clear(page, world):
    dialog = preferences_open(page)
    dialog.get_by_role("button", name="Dusk").click()
    expect(page.locator("body")).to_have_class(re.compile("theme-dusk"))
    until(page, lambda: file_of(world).get("preferences", {}).get("theme") == "dusk", "the file holds the theme")
    page.pw.evaluate("localStorage.clear()")
    page.reload()
    expect(page.locator("body")).to_have_class(re.compile("theme-dusk"))
    clean(page)


def test_a_page_opened_with_no_block_and_nothing_held_writes_no_block(page, world):
    opened(page)
    until(page, lambda: page.pw.evaluate("Boolean(localStorage.getItem('mm_prefs'))"), "the page read its preferences")
    assert "preferences" not in file_of(world)
    clean(page)


def test_what_a_browser_already_held_comes_into_the_file_once_and_is_then_gone(page, world):
    page.pw.add_init_script("""if (!sessionStorage.getItem('seeded')) {
      sessionStorage.setItem('seeded', '1');
      localStorage.setItem('mm_theme', 'dusk');
      localStorage.setItem('mm_nokeys', '1');
      localStorage.setItem('mm_read', '{"width":"wide","size":"xl","font":"mono"}');
      localStorage.setItem('mm_threadview', '0');
    }""")
    opened(page)
    until(page, lambda: page.pw.evaluate("localStorage.getItem('mm_nokeys') === null"), "the old key let go")
    held = file_of(world)["preferences"]
    assert held == {"theme": "dusk", "single_keys": False, "read_width": "wide", "read_size": "xl", "read_font": "mono", "conversations": False}
    assert page.pw.evaluate("['mm_theme','mm_read','mm_threadview'].map(k => localStorage.getItem(k))") == [None, None, None]
    expect(page.locator("body")).to_have_class(re.compile("theme-dusk"))
    world_before = (world.dir / "mailmind.config.json").read_bytes()
    page.reload()
    expect(page.locator("body")).to_have_class(re.compile("theme-dusk"))
    page.keyboard.press("c")                     # still off
    expect(page.locator("#cmpBody")).to_have_count(0)
    assert (world.dir / "mailmind.config.json").read_bytes() == world_before
    clean(page)


def test_the_undo_send_window_is_set_in_preferences_and_held_to_its_bounds(page, world):
    dialog = preferences_open(page)
    field = dialog.get_by_label("Undo Send window, in seconds")
    expect(field).to_have_value("10")
    field.fill("30")
    field.press("Enter")
    until(page, lambda: file_of(world).get("preferences", {}).get("undo_send_seconds") == 30, "the file holds the window")
    field.fill("1")                                     # past the shortest: the field holds it there
    field.press("Enter")
    expect(field).to_have_value("5")
    until(page, lambda: file_of(world)["preferences"]["undo_send_seconds"] == 5, "the bound is what's kept")
    field.fill("9000")
    field.press("Enter")
    expect(field).to_have_value("60")
    until(page, lambda: file_of(world)["preferences"]["undo_send_seconds"] == 60, "the bound is what's kept")
    clean(page)


def padding_of(page):
    return page.locator("#rows .row.cmsg").first.evaluate("e => parseFloat(getComputedStyle(e).paddingTop)")


def test_density_tightens_the_list_and_is_there_from_the_first_paint(page, world):
    dialog = preferences_open(page)
    comfortable = padding_of(page)
    assert page.locator("body").get_attribute("data-density") == "comfortable"
    dialog.get_by_role("button", name="Compact").click()
    expect(page.locator("body")).to_have_attribute("data-density", "compact")
    tight = padding_of(page)
    assert tight < comfortable
    dialog.get_by_role("button", name="Cozy").click()
    expect(page.locator("body")).to_have_attribute("data-density", "cozy")
    assert tight < padding_of(page) < comfortable
    until(page, lambda: file_of(world).get("preferences", {}).get("density") == "cozy", "the file holds the density")
    page.pw.route(re.compile(r"/assets/.*\.js"), lambda route: route.abort() if "theme" not in route.request.url else route.continue_())
    page.reload()                                        # with only theme.js running, the first paint is still cozy
    expect(page.locator("body")).to_have_attribute("data-density", "cozy")


def test_compact_clamps_the_gist_to_a_line_and_hides_the_chip(page, world):
    from mailmind.bootstrap import accounts
    accounts.config.set_preferences({"density": "compact"})
    opened(page)
    probe = page.locator("#rows .row.cmsg").first.evaluate("""row => {
      const gist = document.createElement('div'), chip = document.createElement('div');
      gist.className = 'gist'; chip.className = 'r4'; row.append(gist, chip);
      return [getComputedStyle(gist).webkitLineClamp, getComputedStyle(chip).display];
    }""")
    assert probe == ["1", "none"]
    clean(page)


def test_high_contrast_changes_every_theme_and_is_kept(page, world):
    dialog = preferences_open(page)
    ink = lambda: page.pw.evaluate("getComputedStyle(document.body).getPropertyValue('--ink').trim().toLowerCase()")
    for theme, plain, strong in (("Day", "#24221e", "#000000"), ("Dusk", "#27211c", "#000000"), ("Night", "#ede7d7", "#ffffff")):
        dialog.get_by_role("button", name=theme).click()
        assert ink() == plain
        dialog.get_by_label("High contrast").check()
        expect(page.locator("body")).to_have_class(re.compile(r"\bhc\b"))
        assert ink() == strong
        dialog.get_by_label("High contrast").uncheck()
        expect(page.locator("body")).not_to_have_class(re.compile(r"\bhc\b"))
    dialog.get_by_label("High contrast").check()
    until(page, lambda: file_of(world).get("preferences", {}).get("high_contrast") is True, "the file holds it")
    page.pw.evaluate("localStorage.clear()")
    page.reload()
    expect(page.locator("body")).to_have_class(re.compile(r"\bhc\b"))
    clean(page)


def test_axe_finds_nothing_on_high_contrast(page, world, every_theme):
    from axe_playwright_python.sync_playwright import Axe
    found = lambda: {v["id"]: [n["target"] for n in v["nodes"]] for v in Axe().run(page.pw, options={"iframes": False}).response["violations"]}
    from mailmind.bootstrap import accounts
    accounts.config.set_preferences({"high_contrast": True})
    opened(page)
    expect(page.locator("body")).to_have_class(re.compile(r"\bhc\b"))
    assert found() == {}
    clean(page)

