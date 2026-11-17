"""Setting up a connection in Settings: the form starts at the address, and the server's preset for it picks the type
(`/api/accounts/preset`, through the `dns` fixture's nameserver for a domain's mail servers). It offers Gmail's settings for
an address whose domain Google's servers take, shows how to make an App Password with Google's page a link away, fills in
iCloud's servers with its app-specific password's page beside the password, says plainly that Microsoft's accounts can't be
added yet, and makes any other address Other IMAP, with its incoming and outgoing servers. A connection is tested as soon as
it is saved, saying plainly what refused it and what to do next. A Proton account logs in with the Bridge's password, so it
is tested once it is added, as it is saved."""
import re

import pytest
from playwright.sync_api import expect

from fixtures import world as w

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
    expect(dialog.get_by_label("Email", exact=True)).to_be_focused()
    expect(dialog.get_by_role("group", name="Type")).to_have_count(0)          # no type until the address says which
    dialog.get_by_label("Email", exact=True).fill("dee@acme.example.test")
    offer = dialog.locator(".set-offer")
    expect(offer).to_have_text(re.compile("^Gmail takes acme.example.test’s mail: use Gmail’s settings?"))
    expect(dialog.get_by_role("button", name="Other IMAP", exact=True)).to_have_attribute("aria-pressed", "true")
    offer.get_by_role("button", name="Use Gmail").click()
    expect(dialog.get_by_role("button", name="Gmail", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(offer).to_have_count(0)
    expect(dialog.get_by_text("Gmail takes acme.example.test’s mail: Gmail’s settings fit it.")).to_be_visible()

    steps = dialog.locator(".set-apppw")
    expect(steps.locator("li")).to_have_text(["Turn on 2-Step Verification for your Google Account.",
                                              "Create an App Password, named MailMind.",
                                              "Paste it above as Google shows it, spaces and all."])
    link = steps.get_by_role("link", name="Open Google’s App Passwords")
    expect(link).to_have_attribute("href", "https://myaccount.google.com/apppasswords")
    expect(link).to_have_attribute("target", "_blank")
    expect(link).to_have_attribute("rel", "noopener noreferrer")
    expect(dialog.get_by_label("Password", exact=True)).to_have_attribute("placeholder", "abcd efgh ijkl mnop")

    expect(dialog.get_by_role("button", name="Other IMAP", exact=True)).to_have_attribute("aria-pressed", "false")
    dialog.get_by_label("Email", exact=True).fill("dee@gmail.com")          # Gmail's own: its type, and nothing to offer
    expect(dialog.get_by_text("Gmail address: Gmail’s settings are filled in.")).to_be_visible()
    expect(dialog.get_by_role("button", name="Gmail", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(offer).to_have_count(0)
    assert dns.asked == [("acme.example.test", 15)]
    clean(page)


def test_an_icloud_address_takes_its_servers_and_its_app_specific_passwords_page_is_beside_the_password(page):
    dialog = settings(page)
    page.get_by_role("button", name="+ Add a connection").click()
    dialog.get_by_label("Email", exact=True).fill("dee@icloud.com")
    expect(dialog.get_by_role("button", name="Other IMAP", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(dialog.get_by_label("Host", exact=True)).to_have_value("imap.mail.me.com")
    expect(dialog.get_by_label("Port", exact=True)).to_have_value("993")
    expect(dialog.get_by_label("Outgoing host")).to_have_value("smtp.mail.me.com")
    expect(dialog.get_by_label("Outgoing port")).to_have_value("587")
    expect(dialog.locator(".set-note")).to_contain_text("iCloud Mail takes an app-specific password")
    link = dialog.get_by_role("link", name="Make an app-specific password")
    expect(link).to_have_attribute("href", "https://account.apple.com")
    expect(link).to_have_attribute("rel", "noopener noreferrer")
    clean(page)


def test_a_microsoft_address_is_told_it_cant_be_added_yet_before_any_password_is_asked(page):
    dialog = settings(page)
    page.get_by_role("button", name="+ Add a connection").click()
    dialog.get_by_label("Email", exact=True).fill("dee@outlook.com")
    expect(dialog.locator(".set-note")).to_have_text("Outlook, Hotmail, Live and Microsoft 365 accounts need Microsoft's own "
                                                     "sign-in, which MailMind doesn't support yet.")
    expect(dialog.get_by_label("Password", exact=True)).to_have_count(0)
    expect(dialog.get_by_role("button", name="Save connection")).to_be_disabled()
    clean(page)


def test_a_proton_account_is_tested_once_it_is_added_and_then_as_it_is_saved(page):
    dialog = settings(page)
    page.get_by_role("button", name="+ Add a connection").click()
    dialog.get_by_label("Email", exact=True).fill(DEE)
    expect(dialog.get_by_role("button", name="Other IMAP", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(dialog.get_by_role("button", name="Test connection")).to_be_enabled()
    dialog.get_by_role("button", name="Proton", exact=True).click()
    expect(dialog.get_by_text("Add the account, then test it.")).to_be_visible()
    expect(dialog.get_by_role("button", name="Test connection")).to_be_disabled()
    dialog.get_by_role("button", name="Cancel").click()

    dialog.locator(f'.set-row[data-email="{w.BEA}"]').get_by_role("button", name="Edit").click()
    expect(dialog.get_by_role("button", name="Proton", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(dialog.get_by_text("Add the account, then test it.")).to_have_count(0)
    dialog.get_by_role("button", name="Test connection").click()
    expect(dialog.locator(".formbar ~ .set-tested .set-status")).to_have_text("✓ Connected")
    clean(page)


def test_any_other_address_is_other_imap_with_its_outgoing_server_sent_as_typed(page, world, fake_keychain):
    entry = world.add_account(DEE, password="dee-password")
    fake_keychain.delete(f"account:{DEE}")
    dialog = settings(page)
    page.get_by_role("button", name="+ Add a connection").click()
    dialog.get_by_label("Email", exact=True).fill(DEE)
    expect(dialog.get_by_role("button", name="Other IMAP", exact=True)).to_have_attribute("aria-pressed", "true")
    dialog.get_by_label("Host", exact=True).fill(entry["host"])
    dialog.get_by_label("Port", exact=True).fill(str(entry["port"]))
    dialog.get_by_role("group", name="Incoming server security").get_by_role("button", name="STARTTLS").click()
    dialog.get_by_label("Outgoing host").fill(entry["smtp_host"])
    dialog.get_by_label("Outgoing port").fill(str(entry["smtp_port"]))
    dialog.get_by_label("Password", exact=True).fill("dee-password")
    dialog.get_by_role("button", name="Save connection").click()
    row = dialog.locator(f'.set-row[data-email="{DEE}"]')
    expect(row.locator(".set-status")).to_have_text("✓ Connected")
    assert len([c for c in world.smtp.commands_for("QUIT") if c["user"] == DEE]) >= 1
    clean(page)


def test_a_connection_is_tested_as_soon_as_it_is_saved_and_a_refusal_said_plainly(page, world, fake_keychain):
    entry = world.add_account(DEE, password="abcdefghijklmnop")
    fake_keychain.delete(f"account:{DEE}")
    # Google refuses every login so, as a sync the save starts may log in before the test does.
    world.server.fail("LOGIN", "NO [ALERT] Application-specific password required: "
                      "https://support.google.com/accounts/answer/185833 (Failure)", user=DEE, times=None)
    dialog = settings(page)
    page.get_by_role("button", name="+ Add a connection").click()
    dialog.get_by_label("Email", exact=True).fill(DEE)
    expect(dialog.get_by_role("button", name="Other IMAP", exact=True)).to_have_attribute("aria-pressed", "true")
    dialog.get_by_label("Host", exact=True).fill(entry["host"])
    dialog.get_by_label("Port", exact=True).fill(str(entry["port"]))
    dialog.get_by_role("group", name="Incoming server security").get_by_role("button", name="STARTTLS").click()
    dialog.get_by_label("Password", exact=True).fill("my google password")
    dialog.get_by_role("button", name="Save connection").click()
    row = dialog.locator(f'.set-row[data-email="{DEE}"]')
    expect(row.locator(".set-status")).to_have_text("✕ Gmail needs an App Password here, not your Google password. If Google "
                                                    "offers none, Advanced Protection or a Workspace admin has switched them off.")
    expect(row.locator(".set-status")).to_be_visible()
    expect(row.get_by_role("link", name="Make an App Password")).to_have_attribute("href", "https://myaccount.google.com/apppasswords")
    clean(page)


def test_a_refused_password_says_what_to_do_next_with_its_class_in_small_type(page, world, fake_keychain):
    entry = world.add_account(DEE, password="dee-password")
    fake_keychain.delete(f"account:{DEE}")
    world.server.fail("LOGIN", "NO [AUTHENTICATIONFAILED] Invalid credentials (Failure)", user=DEE, times=None)
    dialog = settings(page)
    page.get_by_role("button", name="+ Add a connection").click()
    dialog.get_by_label("Email", exact=True).fill(DEE)
    expect(dialog.get_by_role("button", name="Other IMAP", exact=True)).to_have_attribute("aria-pressed", "true")
    dialog.get_by_label("Host", exact=True).fill(entry["host"])
    dialog.get_by_label("Port", exact=True).fill(str(entry["port"]))
    dialog.get_by_role("group", name="Incoming server security").get_by_role("button", name="STARTTLS").click()
    dialog.get_by_label("Password", exact=True).fill("not dee's password")
    dialog.get_by_role("button", name="Test connection").click()
    tested = dialog.locator(".formbar ~ .set-tested")
    expect(tested.locator(".set-status")).to_have_text(re.compile("^✕ The server didn't accept that address and password."))
    expect(tested.locator(".set-class")).to_have_text("AUTHENTICATIONFAILED")
    clean(page)
