"""AI in Settings (#49): its own tab, with the switch first, the provider, its key, its model and the URL a service is reached
at, the daily limit and the work written ahead, all shown only once AI mode is on; and a line saying how AI stands. A key goes to the
server's Keychain (the tests' stand-in) and never shows again. Turned off, as a fresh install starts, nothing only AI can do
is offered anywhere, and nothing says it is missing. A model on this Mac is picked from the model servers found running
here: only the fakes `local_ai` names, or none, so no test reaches one that may be running on this computer. Nothing reaches a real provider: OpenAI is the fake
OpenAI-compatible API on loopback, at a URL the test gives the server first.
"""
import json
import re

import pytest
from playwright.sync_api import expect

from fakes import anthropic_api as fa
from fakes import openai_api as fo

pytestmark = pytest.mark.browser

RECEIPT = "ada-inbox-05-receipt.eml"
KEPT = "Kept on this Mac, with each key in your login Keychain."
SENDS = ("With AI mode on, each new message’s sender, subject and opening text go to Anthropic to be summarized, automatically. "
         "Whole conversations go when you ask for a summary, a draft or the brief, with what you type into Ask, a search written "
         "as a sentence, or Rewrite. Attachments never leave this Mac.")


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


def ai_open(page, app=None):
    """The AI tab, with AI mode turned on first where `app` is given: a fresh install starts with it off."""
    if app is not None:
        assert app.post("/api/preferences", {"ai_enabled": True}).status == 200
    page.goto("/")
    page.get_by_role("tab", name=re.compile("^Everything")).click()
    expect(page.get_by_role("option").first).to_be_visible()
    page.locator("#settingsBtn").click()
    page.get_by_role("tab", name="AI").click()
    dialog = page.get_by_role("dialog", name="AI")
    expect(dialog).to_be_visible()
    return dialog


def test_a_fresh_install_starts_with_ai_mode_off_and_turned_on_says_how_ai_stands(page, world):
    dialog = ai_open(page)
    switch = dialog.get_by_label("AI mode", exact=True)
    expect(switch).not_to_be_checked()
    expect(dialog.get_by_text("Summaries, the brief, drafts and Ask. Off, MailMind sends nothing to any AI service.")).to_be_visible()
    expect(page.locator("#aiSends")).to_have_text(SENDS)            # what would go, said before it is turned on
    expect(page.locator("#setSub")).to_have_text(KEPT)
    expect(page.locator("#aiConnection")).to_have_count(0)
    switch.check()
    expect(page.locator("#aiSends")).to_have_text(SENDS)
    expect(page.locator("#setSub")).to_have_text(KEPT)
    expect(dialog.get_by_label("Provider", exact=True)).to_have_value("anthropic")
    expect(dialog.get_by_label("Provider", exact=True).locator("option")).to_have_text(
        ["Claude (Anthropic)", "OpenAI", "Google Gemini", "A model on this Mac", "Another OpenAI-compatible service"])
    link = dialog.get_by_role("link", name=re.compile(r"^Make a key at console\.anthropic\.com"))
    expect(link).to_have_attribute("href", "https://console.anthropic.com")
    expect(link).to_have_attribute("rel", "noopener noreferrer")
    expect(page.locator("#aiBilling")).to_have_text("Billed per use by Anthropic, separately from any ChatGPT or Claude "
                                                    "subscription. Add credit in Anthropic’s console, under Billing.")
    credit = dialog.get_by_role("link", name=re.compile(r"^Add credit at console\.anthropic\.com"))
    expect(credit).to_have_attribute("href", "https://console.anthropic.com/settings/billing")
    expect(dialog.get_by_text("Keep a key for Claude, and its models are listed here.")).to_be_visible()
    expect(page.locator("#aiStanding")).to_have_text("AI is off: there’s no key for Claude.")
    page.get_by_role("tab", name="Preferences").click()           # what the file holds: not the AI tab's words
    expect(page.locator("#setSub")).to_have_text("Stored locally in mailmind.config.json · never leaves this machine")
    clean(page)


AI_ONLY = ("Provider", "API key", "Model", "Daily limit, in US dollars", "Have today’s brief ready each morning",
           "Have last week’s review ready each Monday", "File low-priority mail by itself, once a day",
           "Draft replies ahead for important mail")


def test_with_ai_mode_off_the_tab_holds_only_its_switch_and_the_file_holds_it(page, app, world):
    dialog = ai_open(page, app)
    for shown in AI_ONLY:
        expect(dialog.get_by_label(shown, exact=True)).to_be_visible()
    with page.pw.expect_response(lambda r: r.url.endswith("/api/digest") and r.request.method == "GET"):
        dialog.get_by_label("AI mode", exact=True).uncheck()
    until(page, lambda: file_of(world).get("preferences", {}).get("ai_enabled") is False, "the file holds the switch")
    for gone in AI_ONLY:
        expect(dialog.get_by_label(gone, exact=True)).to_have_count(0)
    expect(page.locator("#aiConnection")).to_have_count(0)
    expect(page.locator("#aiStanding")).to_have_count(0)
    expect(dialog.get_by_text("Summaries, the brief, drafts and Ask. Off, MailMind sends nothing to any AI service.")).to_be_visible()

    dialog.get_by_label("AI mode", exact=True).check()
    for shown in AI_ONLY:
        expect(dialog.get_by_label(shown, exact=True)).to_be_visible()
    expect(page.locator("#aiStanding")).to_have_text("AI is off: there’s no key for Claude.")
    until(page, lambda: file_of(world).get("preferences", {}).get("ai_enabled") is not False, "the switch is on again")
    clean(page)


def test_drafting_ahead_starts_off_and_its_switch_is_kept_in_the_file(page, app, world):
    dialog = ai_open(page, app)
    ahead = dialog.get_by_label("Draft replies ahead for important mail", exact=True)
    expect(ahead).not_to_be_checked()
    ahead.check()
    until(page, lambda: file_of(world).get("preferences", {}).get("draft_ahead") is True, "the file holds the switch")
    ahead.uncheck()
    until(page, lambda: file_of(world).get("preferences", {}).get("draft_ahead") is False, "the file holds it off")
    clean(page)


def test_openai_is_chosen_its_key_kept_through_the_server_and_its_models_offered(page, app, world, openai_api, fake_keychain):
    dialog = ai_open(page, app)
    with page.pw.expect_response(lambda r: r.url.endswith("/api/preferences") and r.request.method == "POST"):
        dialog.get_by_label("Provider", exact=True).select_option("openai")
    expect(page.locator("#aiStanding")).to_have_text("AI is off: there’s no key for OpenAI.")
    expect(dialog.get_by_role("link", name=re.compile(r"^Make a key at platform\.openai\.com"))).to_have_attribute(
        "href", "https://platform.openai.com/api-keys")
    expect(page.locator("#aiBilling")).to_contain_text("Billed per use by OpenAI, separately from any ChatGPT or Claude subscription.")
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
    expect(page.locator("#aiModelsUsed")).to_have_text("Summaries made in the background use gpt-6-luna; everything you ask "
                                                       "for uses gpt-6.1-sol.")
    expect(page.locator("#aiWaiting")).to_have_text(re.compile(r"^\d+ messages from the last 30 days wait for a summary\.$"))
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


def test_a_key_kept_in_place_of_a_refused_one_has_the_provider_asked_for_its_models_again(page, app, world, openai_api, fake_keychain):
    mistyped = "sk-test-mistyped-openai-key-9876543210"         # fictional, and not the one the fake takes
    dialog = ai_open(page, app)
    with page.pw.expect_response(lambda r: r.url.endswith("/api/preferences") and r.request.method == "POST"):
        dialog.get_by_label("Provider", exact=True).select_option("openai")
    assert app.post("/api/preferences", {"ai_base_url": openai_api.url}).status == 200     # the fake in OpenAI's place
    field = dialog.get_by_label("API key", exact=True)
    model = dialog.get_by_label("Model", exact=True)

    field.fill(mistyped)
    dialog.get_by_role("button", name="Keep key").click()
    expect(dialog.get_by_text("The provider refused the key.")).to_be_visible()
    expect(model.locator("option")).to_have_text(["Default"])

    field.fill(fo.KEY)                                          # a key in place of the one kept
    with page.pw.expect_response(lambda r: "/api/ai/models" in r.url and r.status == 200):
        dialog.get_by_role("button", name="Keep key").click()
    expect(model.locator("option")).to_have_text(["Default (gpt-6.1-sol)", "gpt-6-luna"])
    expect(dialog.get_by_text("The provider refused the key.")).to_have_count(0)
    expect(page.locator("#aiStanding")).to_have_text("AI is on: OpenAI · gpt-6.1-sol.")
    assert [(r.path, r.key) for r in openai_api.requests] == [("/models", mistyped), ("/models", fo.KEY)]
    assert all("status of 400" in p for p in page.problems), page.problems     # the refusal of the first key
    page.problems.clear()
    clean(page)


def local_found(page):
    return page.pw.expect_response(lambda r: r.url.endswith("/api/ai/local") and r.request.method == "GET")


def test_a_model_on_this_mac_is_picked_from_the_servers_found_with_its_url(local_ai, page, app, world):
    local_ai["Ollama"].models = ["qwen3:8b", "llama3.3:latest"]
    local_ai["LM Studio"].models = ["mistral-small"]
    dialog = ai_open(page, app)
    with local_found(page):
        dialog.get_by_label("Provider", exact=True).select_option("local")
    model = dialog.get_by_label("Model", exact=True)
    expect(model.locator("optgroup")).to_have_count(2)
    assert [g.get_attribute("label") for g in model.locator("optgroup").all()] == ["Ollama", "LM Studio"]
    expect(model.locator("option")).to_have_text(["Choose a model", "llama3.3:latest", "qwen3:8b", "mistral-small"])
    expect(dialog.get_by_text("Running on this Mac: Ollama and LM Studio.")).to_be_visible()
    expect(dialog.get_by_label("API key (optional)", exact=True)).to_be_visible()
    expect(dialog.get_by_text("Only if your server asks for one.", exact=False)).to_be_visible()
    expect(page.locator("#aiContext")).to_have_text("In Ollama’s settings, set Context length to 16K or more.")
    expect(page.locator("#aiGetOllama")).to_have_count(0)               # a server is running: nothing to get
    expect(page.locator("#aiStanding")).to_have_text("AI is off: no model on this Mac is chosen.")
    expect(page.locator("#aiSends")).to_have_text("With a model on this Mac, nothing leaves this Mac.")

    model.select_option(label="mistral-small")                 # LM Studio's: its URL and the model, in one change
    held = lambda: file_of(world).get("preferences", {})
    until(page, lambda: held().get("ai_model") == "mistral-small", "the file holds the model")
    assert held()["ai_base_url"] == local_ai["LM Studio"].url
    expect(dialog.get_by_label("Your model server’s URL", exact=True)).to_have_value(local_ai["LM Studio"].url)
    expect(page.locator("#aiStanding")).to_have_text("AI is on: A model on this Mac · mistral-small.")
    expect(model.locator("option:checked")).to_have_text("mistral-small")

    model.select_option(label="qwen3:8b")                      # Ollama's
    until(page, lambda: held().get("ai_base_url") == local_ai["Ollama"].url, "the file holds Ollama's URL")
    assert held()["ai_model"] == "qwen3:8b"
    expect(page.locator("#aiStanding")).to_have_text("AI is on: A model on this Mac · qwen3:8b.")
    assert local_ai["Ollama"].requests[-1].path == "/models" and local_ai["LM Studio"].requests[-1].path == "/models"
    clean(page)


def test_with_no_model_server_running_it_says_to_start_one_and_takes_a_url_and_a_model_typed(page, app, world, openai_api):
    openai_api.key = None
    dialog = ai_open(page, app)
    with local_found(page):
        dialog.get_by_label("Provider", exact=True).select_option("local")
    none = "No model server is running on this Mac yet."
    expect(dialog.get_by_text(none)).to_be_visible()
    steps = page.locator("#aiGetOllama li")
    expect(steps).to_have_text(["Get Ollama and open it.",
                                "In Ollama, download a model. A good first one is qwen3:8b, as Ollama lists it: it runs well on a Mac "
                                "with 16 GB of memory.",
                                "In Ollama’s settings, set Context length to 16K or more.", "Then look again."])
    get = dialog.get_by_role("link", name="Get Ollama")
    expect(get).to_have_attribute("href", "https://ollama.com")
    expect(get).to_have_attribute("rel", "noopener noreferrer")
    expect(dialog.get_by_label("Model", exact=True).locator("option")).to_have_text(["Choose a model"])
    with local_found(page):
        dialog.get_by_role("button", name="Look again").click()
    expect(dialog.get_by_text(none)).to_be_visible()

    url = dialog.get_by_label("Your model server’s URL", exact=True)
    expect(url).to_have_value("http://127.0.0.1:11434/v1")
    url.fill("https://llm.example.test/v1")                     # off this Mac: refused in the server's words
    with page.pw.expect_response(lambda r: r.url.endswith("/api/preferences") and r.status == 400):
        url.press("Enter")
    expect(page.locator("#toast")).to_contain_text("A model on this Mac must be at 127.0.0.1, ::1 or localhost.")
    expect(url).to_have_value("http://127.0.0.1:11434/v1")
    assert all("status of 400" in p for p in page.problems), page.problems
    page.problems.clear()

    url.fill(openai_api.url + "/")
    url.press("Enter")
    until(page, lambda: file_of(world).get("preferences", {}).get("ai_base_url") == openai_api.url, "the file holds the URL")
    dialog.get_by_role("button", name="Type a name").click()
    dialog.get_by_label("Model", exact=True).fill("llama3.3:70b")
    dialog.get_by_role("button", name="Use it").click()
    until(page, lambda: file_of(world).get("preferences", {}).get("ai_model") == "llama3.3:70b", "the file holds the model typed")
    expect(page.locator("#aiStanding")).to_have_text("AI is on: A model on this Mac · llama3.3:70b.")
    expect(dialog.get_by_label("Model", exact=True).locator("option:checked")).to_have_text("llama3.3:70b")
    assert openai_api.requests == []                            # saving a URL asks nothing of it
    clean(page)


def test_the_focus_stays_where_a_person_was_as_a_url_a_model_typed_or_a_key_removed_is_saved(page, app, world, openai_api, fake_keychain):
    openai_api.key = None                                       # a compatible service that takes any key, or none
    dialog = ai_open(page, app)
    with page.pw.expect_response(lambda r: r.url.endswith("/api/preferences") and r.request.method == "POST"):
        dialog.get_by_label("Provider", exact=True).select_option("compatible")
    held = lambda: file_of(world).get("preferences", {})

    url = dialog.get_by_label("The service’s URL", exact=True)
    url.fill(openai_api.url)
    url.press("Enter")
    until(page, lambda: held().get("ai_base_url") == openai_api.url, "the file holds the URL")
    expect(url).to_be_focused()
    expect(url).to_have_value(openai_api.url)

    dialog.get_by_role("button", name="Type a name").click()
    typed = dialog.get_by_label("Model", exact=True)
    expect(typed).to_be_focused()
    typed.fill("gpt-6-luna")
    typed.press("Enter")
    until(page, lambda: held().get("ai_model") == "gpt-6-luna", "the file holds the model typed")
    menu = dialog.get_by_label("Model", exact=True)
    expect(menu.locator("option:checked")).to_have_text("gpt-6-luna")
    expect(menu).to_be_focused()
    dialog.get_by_role("button", name="Type a name").click()
    dialog.get_by_role("button", name="Pick from the list").click()
    expect(menu.locator("option:checked")).to_have_text("gpt-6-luna")
    expect(menu).to_be_focused()

    key = dialog.get_by_label("API key (optional)", exact=True)
    key.fill(fo.KEY)
    key.press("Enter")
    expect(dialog.get_by_text("A key is kept in the Keychain")).to_be_visible()
    dialog.get_by_role("button", name="Remove").click()
    expect(dialog.get_by_text("A key is kept in the Keychain")).to_have_count(0)
    expect(key).to_be_focused()
    clean(page)


def test_a_services_models_are_asked_for_once_the_url_given_is_saved(page, app, world, openai_api):
    openai_api.key = None
    dialog = ai_open(page, app)
    with page.pw.expect_response(lambda r: r.url.endswith("/api/preferences") and r.request.method == "POST"):
        dialog.get_by_label("Provider", exact=True).select_option("compatible")
    expect(dialog.get_by_text("Give the service’s URL, and its models are listed here.")).to_be_visible()
    listings = []
    page.on("response", lambda r: listings.append(r.status) if "/api/ai/models" in r.url else None)
    url = dialog.get_by_label("The service’s URL", exact=True)
    url.fill(openai_api.url)
    url.press("Enter")
    expect(dialog.get_by_label("Model", exact=True).locator("option")).to_have_text(["Choose a model", "gpt-6-luna", "gpt-6.1-sol"])
    page.heard_all_asked()
    assert listings == [200]                                    # never before the server held the URL, to be told to give one
    assert [r.path for r in openai_api.requests] == ["/models"]
    clean(page)


def test_the_spending_card_says_the_last_week_and_this_month_by_feature_and_where_the_bill_is(page, app, world, ai):
    status, said = fa.answer({"text": "the"})
    ai.queue((status, {**said, "usage": {"input_tokens": 10_000, "output_tokens": 0}}))
    assert app.post("/api/ai/rewrite", {"op": "fix", "text": "teh"}).status == 200
    ai_open(page, app)
    spent = page.locator("#aiSpent")
    expect(spent).to_contain_text(re.compile(r"The last 7 days: \$\d+\.\d\d, 1 call \(Rewrite \$\d+\.\d\d\)\."))
    expect(spent).to_contain_text(re.compile(r"This month: \$\d+\.\d\d, 1 call \(Rewrite \$\d+\.\d\d\)\."))
    expect(spent).to_contain_text("MailMind counts these from each call it makes; Anthropic’s console holds the bill.")
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
        page.get_by_role("dialog", name="AI").get_by_label("AI mode", exact=True).uncheck()
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
    page.keyboard.press("?")                            # the legend: no ? or !, and that nothing is sent, and what would be
    keys = page.get_by_role("dialog", name="Keyboard")
    expect(keys).to_contain_text("AI is turned off in Settings, so MailMind sends nothing to any AI service")
    expect(keys).to_contain_text(SENDS)
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
