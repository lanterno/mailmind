"""AI in Settings (#49): its own tab, with the switch first, the provider, its key, its model and the URL a service is reached
at; the daily limit and the work written ahead shown only while AI is on; and a line saying how AI stands. A key goes to the
server's Keychain (the tests' stand-in) and never shows again. Turned off, nothing only AI can do is offered anywhere, and
nothing says it is missing. Nothing reaches a real provider: OpenAI is the fake
OpenAI-compatible API on loopback, at a URL the test gives the server first.
"""
import json
import re

import pytest
from playwright.sync_api import expect

from fakes import openai_api as fo

pytestmark = pytest.mark.browser

RECEIPT = "ada-inbox-05-receipt.eml"


def clean(page):
    assert page.problems == [], page.problems
    assert page.blocked == [], page.blocked


def until(page, check, what):
    for _ in range(100):
        if check():
            return
        page.wait_for_timeout(50)
    assert check(), what


def file_of(world):
    return json.loads((world.dir / "mailmind.config.json").read_text())


def ai_open(page):
    page.goto("/")
    page.get_by_role("tab", name=re.compile("^Everything")).click()
    expect(page.get_by_role("option").first).to_be_visible()
    page.locator("#settingsBtn").click()
    page.get_by_role("tab", name="AI").click()
    dialog = page.get_by_role("dialog", name="AI")
    expect(dialog).to_be_visible()
    return dialog


def test_the_ai_tab_puts_the_switch_first_and_says_how_ai_stands(page, world):
    dialog = ai_open(page)
    switch = dialog.get_by_label("Use AI", exact=True)
    expect(switch).to_be_checked()
    expect(dialog.get_by_text("Summaries, the brief, drafts and Ask. Off, MailMind sends nothing to any AI service.")).to_be_visible()
    expect(dialog.get_by_label("Provider", exact=True)).to_have_value("anthropic")
    expect(dialog.get_by_label("Provider", exact=True).locator("option")).to_have_text(
        ["Claude (Anthropic)", "OpenAI", "Google Gemini", "Ollama, on this Mac", "Another OpenAI-compatible service"])
    link = dialog.get_by_role("link", name=re.compile(r"^Make a key at console\.anthropic\.com"))
    expect(link).to_have_attribute("href", "https://console.anthropic.com")
    expect(link).to_have_attribute("rel", "noopener noreferrer")
    expect(dialog.get_by_text("Keep a key for Claude, and its models are listed here.")).to_be_visible()
    expect(page.locator("#aiStanding")).to_have_text("AI is off: there’s no key for Claude.")
    clean(page)


def test_turning_ai_off_takes_away_its_limit_and_its_work_ahead_and_goes_in_the_file(page, world):
    dialog = ai_open(page)
    expect(dialog.get_by_label("Daily limit, in US dollars")).to_be_visible()
    with page.pw.expect_response(lambda r: r.url.endswith("/api/digest") and r.request.method == "GET"):
        dialog.get_by_label("Use AI", exact=True).uncheck()
    until(page, lambda: file_of(world).get("preferences", {}).get("ai_enabled") is False, "the file holds the switch")
    for gone in ("Daily limit, in US dollars", "Have today’s brief ready each morning", "Have last week’s review ready each Monday",
                 "File low-priority mail by itself, once a day"):
        expect(dialog.get_by_label(gone)).to_have_count(0)
    expect(page.locator("#aiStanding")).to_have_text("AI is turned off.")
    dialog.get_by_label("Use AI", exact=True).check()
    expect(dialog.get_by_label("Daily limit, in US dollars")).to_be_visible()
    until(page, lambda: file_of(world).get("preferences", {}).get("ai_enabled") is not False, "the switch is on again")
    clean(page)


def test_openai_is_chosen_its_key_kept_through_the_server_and_its_models_offered(page, app, world, openai_api, fake_keychain):
    dialog = ai_open(page)
    with page.pw.expect_response(lambda r: r.url.endswith("/api/preferences") and r.request.method == "POST"):
        dialog.get_by_label("Provider", exact=True).select_option("openai")
    expect(page.locator("#aiStanding")).to_have_text("AI is off: there’s no key for OpenAI.")
    expect(dialog.get_by_role("link", name=re.compile(r"^Make a key at platform\.openai\.com"))).to_have_attribute(
        "href", "https://platform.openai.com/api-keys")
    expect(dialog.get_by_text("Keep a key for OpenAI, and its models are listed here.")).to_be_visible()
    assert openai_api.requests == []
    assert app.post("/api/preferences", {"ai_base_url": openai_api.url}).status == 200     # the fake in OpenAI's place

    field = dialog.get_by_label("API key", exact=True)
    field.fill(fo.KEY)
    dialog.get_by_role("button", name="Keep key").click()
    expect(field).to_have_value("")
    expect(dialog.get_by_text("A key is kept in the Keychain")).to_be_visible()
    expect(dialog.get_by_label("Model", exact=True).locator("option")).to_have_text(["Default (gpt-6.1-sol)", "gpt-6-luna"])
    expect(page.locator("#aiStanding")).to_have_text("AI is on: OpenAI · gpt-6.1-sol.")
    assert fake_keychain.items()["openai-api-key"] == fo.KEY
    assert file_of(world)["openai_api_key"] == {"keychain": "openai-api-key", "version": 1}
    assert fo.KEY not in (world.dir / "mailmind.config.json").read_text()
    assert [(r.method, r.path, r.key) for r in openai_api.requests] == [("GET", "/models", fo.KEY)]
    assert fo.KEY not in page.content()

    with page.pw.expect_response(lambda r: r.url.endswith("/api/preferences") and r.request.method == "POST"):
        dialog.get_by_label("Model", exact=True).select_option("gpt-6-luna")
    until(page, lambda: file_of(world).get("preferences", {}).get("ai_model") == "gpt-6-luna", "the file holds the model")
    expect(page.locator("#aiStanding")).to_have_text("AI is on: OpenAI · gpt-6-luna.")

    dialog.get_by_role("button", name="Remove").click()
    expect(dialog.get_by_text("A key is kept in the Keychain")).to_have_count(0)
    expect(page.locator("#aiStanding")).to_have_text("AI is off: there’s no key for OpenAI.")
    assert "openai-api-key" not in fake_keychain.items() and "openai_api_key" not in file_of(world)
    clean(page)


def test_ollamas_url_is_filled_in_and_a_model_typed_by_hand(page, app, world, openai_api):
    openai_api.key = None
    openai_api.models = ["qwen3:8b", "llama3.3:latest"]
    # Off, nothing asks Ollama at its own URL, where a real one may be listening on this computer.
    assert app.post("/api/preferences", {"ai_provider": "ollama", "ai_enabled": False}).status == 200
    asked = []
    page.on("request", lambda r: asked.append(r.url))
    dialog = ai_open(page)
    expect(dialog.get_by_label("API key", exact=True)).to_have_count(0)
    url = dialog.get_by_label("The service’s URL", exact=True)
    expect(url).to_have_value("http://127.0.0.1:11434/v1")
    expect(dialog.get_by_text("Ollama has no model of its own: choose one.")).to_be_visible()
    expect(page.locator("#aiStanding")).to_have_text("AI is turned off.")
    page.heard_all_asked()
    assert not [u for u in asked if "/api/ai/models" in u]

    url.fill(openai_api.url + "/")
    with page.pw.expect_response(lambda r: "/api/ai/models" in r.url and r.ok):
        url.press("Enter")
    until(page, lambda: file_of(world).get("preferences", {}).get("ai_base_url") == openai_api.url, "the file holds the URL")
    expect(dialog.get_by_label("Model", exact=True).locator("option")).to_have_text(["Choose a model", "llama3.3:latest", "qwen3:8b"])

    dialog.get_by_role("button", name="Type a name").click()
    dialog.get_by_label("Model", exact=True).fill("llama3.3:70b")
    dialog.get_by_role("button", name="Use it").click()
    until(page, lambda: file_of(world).get("preferences", {}).get("ai_model") == "llama3.3:70b", "the file holds the model typed")
    expect(dialog.get_by_label("Model", exact=True)).to_have_value("llama3.3:70b")
    dialog.get_by_label("Use AI", exact=True).check()
    expect(page.locator("#aiStanding")).to_have_text("AI is on: Ollama, on this Mac · llama3.3:70b.")
    clean(page)


def test_with_ai_turned_off_nothing_only_ai_can_do_is_offered_and_nothing_says_why(page, world, ai):
    asked = []
    page.on("request", lambda r: asked.append(r.url))
    page.goto("/")
    search = page.locator("#search")
    expect(search).to_have_attribute("placeholder", re.compile("^Ask a question"))
    expect(page.locator('[data-brief="week"]')).to_be_visible()
    expect(page.locator('[data-brief="refresh"]')).to_be_visible()
    page.get_by_role("tab", name=re.compile("^Everything")).click()
    page.locator('#rows .row[data-key="{key}"][data-folder="{folder}"]'.format(**world.copy(RECEIPT))).click()
    expect(page.locator('[data-ca="summarize"]')).to_be_visible()
    expect(page.locator('[data-ca="extract"]')).to_be_visible()

    page.locator("#settingsBtn").click()
    page.get_by_role("tab", name="AI").click()
    with page.pw.expect_response(lambda r: r.url.endswith("/api/digest") and r.request.method == "GET"):
        page.get_by_role("dialog", name="AI").get_by_label("Use AI", exact=True).uncheck()
    page.locator("#setClose").click()
    calls = len(ai.requests)

    for gone in ('[data-ca="summarize"]', '[data-ca="extract"]', "#aiCard", "#sgCard"):
        expect(page.locator(gone)).to_have_count(0)
    expect(search).to_have_attribute("placeholder", "Search — is:unread, from:amazon")
    page.keyboard.press("i")                            # what summarized is no key now
    expect(page.locator("#aiCard")).to_have_count(0)
    page.locator("#statusBtn").click()
    menu = page.get_by_role("dialog", name="Sync status")
    expect(menu).to_be_visible()
    expect(menu.locator(".sm-ai")).to_have_count(0)
    expect(menu).not_to_contain_text("summarized")
    page.keyboard.press("Escape")
    page.keyboard.press("?")                            # the legend: no ? or !, and only that nothing is sent
    keys = page.get_by_role("dialog", name="Keyboard")
    expect(keys).to_contain_text("AI is turned off in Settings, so MailMind sends nothing to any AI service")
    expect(keys).not_to_contain_text("?question")
    page.keyboard.press("Escape")

    page.goto("/")                                      # the brief: its tallies and cards, nothing of AI's
    expect(page.locator(".bf-card")).to_be_visible()
    for gone in ('[data-brief="refresh"]', '[data-brief="week"]', ".bf-aioff", ".bf-narr", '[data-brief="tidy"]'):
        expect(page.locator(gone)).to_have_count(0)
    search.fill("?when is lunch")                       # a question is words to search for
    search.press("Enter")
    page.expect_where(r"^/search\?q=%3Fwhen")
    expect(page.locator(".ask-wrap")).to_have_count(0)
    expect(page.locator("body")).not_to_contain_text("AI is off")
    page.heard_all_asked()
    assert not [u for u in asked if "/api/ai/ask" in u or "/api/ai/do-plan" in u]
    assert len(ai.requests) == calls
    clean(page)
