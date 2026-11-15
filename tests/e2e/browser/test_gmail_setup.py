"""Setting up a Gmail connection in Settings: the form offers Gmail's settings for an address whose domain Google's servers
take, as the server says (`/api/accounts/preset`, through the `dns` fixture's nameserver), shows how to make an App
Password with Google's page a link away, and tests a connection as soon as it is saved, saying plainly what refused it."""
import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser

DEE = "dee@example.test"


def settings(page):
    page.goto("/")
    page.get_by_role("tab", name=re.compile("^Everything")).click()
    expect(page.get_by_role("option").first).to_be_visible()
    page.locator("#settingsBtn").click()
    return page.get_by_role("dialog", name="Email connections")


def clean(page):
    assert page.problems == [], page.problems
    assert page.blocked == [], page.blocked


def test_an_address_google_takes_the_mail_of_is_offered_gmails_settings_and_how_to_make_an_app_password(page, dns):
    dns.mx("acme.example.test", ["aspmx.l.google.com", "alt1.aspmx.l.google.com"])
    dialog = settings(page)
    page.get_by_role("button", name="+ Add a connection").click()
    dialog.get_by_role("button", name="IMAP", exact=True).click()
    dialog.get_by_label("Email", exact=True).fill("dee@acme.example.test")
    offer = dialog.locator(".set-offer")
    expect(offer).to_have_text(re.compile("^Google takes acme.example.test’s mail: use Gmail’s settings?"))
    offer.get_by_role("button", name="Use Gmail").click()
    expect(dialog.get_by_role("button", name="Gmail", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(offer).to_have_count(0)
    expect(dialog.get_by_text("Google takes acme.example.test’s mail: Gmail’s settings fit it.")).to_be_visible()

    steps = dialog.locator(".set-apppw")
    expect(steps.locator("li")).to_have_text(["Turn on 2-Step Verification for your Google Account.",
                                              "Create an App Password, named MailMind.",
                                              "Paste it above as Google shows it, spaces and all."])
    link = steps.get_by_role("link", name="Open Google’s App Passwords")
    expect(link).to_have_attribute("href", "https://myaccount.google.com/apppasswords")
    expect(link).to_have_attribute("target", "_blank")
    expect(link).to_have_attribute("rel", "noopener noreferrer")
    expect(dialog.get_by_label("Password", exact=True)).to_have_attribute("placeholder", "abcd efgh ijkl mnop")

    dialog.get_by_label("Email", exact=True).fill("dee@gmail.com")          # Gmail's own: nothing to offer, or to say
    dialog.get_by_role("button", name="IMAP", exact=True).click()
    expect(offer).to_have_text(re.compile("^A Google address: use Gmail’s settings?"))
    assert dns.asked == [("acme.example.test", 15)]
    clean(page)


def test_a_connection_is_tested_as_soon_as_it_is_saved_and_a_refusal_said_plainly(page, world, fake_keychain):
    entry = world.server.add_account(DEE, password="abcdefghijklmnop")
    fake_keychain.delete(f"account:{DEE}")
    world.server.fail("LOGIN", "NO [ALERT] Application-specific password required: "
                      "https://support.google.com/accounts/answer/185833 (Failure)", user=DEE)
    dialog = settings(page)
    page.get_by_role("button", name="+ Add a connection").click()
    dialog.get_by_label("Email", exact=True).fill(DEE)
    dialog.get_by_role("button", name="IMAP", exact=True).click()
    dialog.get_by_label("Host").fill(entry["host"])
    dialog.get_by_label("Port").fill(str(entry["port"]))
    dialog.get_by_role("button", name="STARTTLS", exact=True).click()
    dialog.get_by_label("Password", exact=True).fill("my google password")
    dialog.get_by_role("button", name="Save connection").click()
    row = dialog.locator(f'.set-row[data-email="{DEE}"]')
    expect(row.locator(".set-status")).to_have_text("✕ Gmail needs an App Password here, not your Google password. If Google "
                                                    "offers none, Advanced Protection or a Workspace admin has switched them off.")
    expect(row.locator(".set-status")).to_be_visible()
    clean(page)
