"""mailmind.accounts.domain.login_words: what a person is told of a login that didn't work, by its class and the provider it
was tried at, and the page that helps. Every sentence says what to do next; none holds what a server said. The Rust core's
crates/mm-accounts/tests/login_words.rs holds the same."""
import pytest

from mailmind.accounts.domain import conn_error
from mailmind.accounts.domain.account import PORT_SAID
from mailmind.accounts.domain.login_words import Said, said_of
from mailmind.accounts.domain.preset import GOOGLE_APP_PASSWORDS, PROTON_BRIDGE


def imap(email, host="imap.example.test"):
    return {"kind": "imap", "host": host, "email": email}


@pytest.mark.parametrize("whose, name, url", [
    (imap("ada@example.test", "imap.mail.me.com"), "iCloud", "https://account.apple.com"),
    (imap("ada@icloud.com"), "iCloud", "https://account.apple.com"),
    (imap("ada@yahoo.co.uk"), "Yahoo", "https://login.yahoo.com/account/security"),
    (imap("ada@example.test", "imap.fastmail.com"), "Fastmail", "https://www.fastmail.help/hc/en-us/articles/360058752854"),
], ids=["icloud-host", "icloud-domain", "yahoo", "fastmail"])
def test_a_refused_password_at_a_provider_that_takes_app_passwords_sends_to_its_page(whose, name, url):
    said = said_of("AUTHENTICATIONFAILED", **whose)
    assert said.says.startswith(f"{name} didn't accept that password.") and "app password" in said.says
    assert said.link is not None and said.link.url == url


def test_a_refused_password_anywhere_else_says_to_check_it():
    said = said_of("AUTHENTICATIONFAILED", **imap("ada@example.test"))
    assert said.link is None and said.says.startswith("The server didn't accept that address and password.")


def test_microsofts_accounts_hear_that_they_need_its_sign_in():
    assert "Microsoft's own sign-in" in said_of("AUTHENTICATIONFAILED", **imap("ada@outlook.com", "outlook.office365.com")).says


def test_gmails_refusals_keep_their_sentences_and_the_app_password_ones_send_to_google():
    gmail = {"kind": "gmail", "host": None, "email": "cy@gmail.com"}
    assert said_of(conn_error.GMAIL_REFUSED, **gmail) == Said(conn_error.GMAIL_REFUSED, GOOGLE_APP_PASSWORDS)
    assert said_of(conn_error.SAID[conn_error.APP_PASSWORD], **gmail).link == GOOGLE_APP_PASSWORDS
    imap_off = conn_error.SAID[conn_error.IMAP_OFF]
    assert said_of(imap_off, **gmail) == Said(imap_off, None)


def test_a_bridge_that_refuses_a_connection_isnt_running():
    proton = {"kind": "proton", "host": "127.0.0.1", "email": "pia@proton.me"}
    said = said_of(conn_error.quiet(ConnectionRefusedError(61, "Connection refused")), **proton)
    assert said.says.startswith("Proton Bridge isn't running.") and said.link == PROTON_BRIDGE
    assert "the password Bridge shows" in said_of("AUTHENTICATIONFAILED", **proton).says


@pytest.mark.parametrize("error", [ConnectionRefusedError(61, "Connection refused"), TimeoutError("timed out"),
                                   OSError("nodename nor servname provided, or not known")], ids=lambda e: type(e).__name__)
def test_a_server_that_didnt_answer_says_to_check_its_name_and_port(error):
    assert "Check the server's name and port" in said_of(conn_error.quiet(error), **imap("ada@example.test")).says


def test_a_certificate_not_trusted_says_no_password_was_sent():
    said = said_of("SSLCertVerificationError", **imap("ada@example.test"))
    assert said.says.startswith("The server's certificate isn't trusted, so MailMind didn't send your password.")
    assert "trust the new one in Settings" in said_of("certificate changed", **imap("ada@example.test")).says


@pytest.mark.parametrize("error", ["AUTHENTICATIONFAILED", "connection refused", "SSLCertVerificationError", "SSLError",
                                   "SMTPServerDisconnected"])
def test_the_outgoing_servers_trouble_says_reading_mail_works(error):
    said = said_of(error, "proton", "127.0.0.1", "pia@proton.me", outgoing=True)
    assert said.says.startswith("Reading mail works, but")


def test_a_port_no_connection_could_reach_says_what_a_port_is():
    assert said_of(PORT_SAID["port"], **imap("ada@example.test")).says == "The port must be a number from 1 to 65535."
    assert said_of(PORT_SAID["smtp_port"], **imap("ada@example.test")).says.startswith("The outgoing server's port")


def test_anything_else_says_what_to_check():
    said = said_of("ConnectionLost", **imap("ada@example.test"))
    assert said == Said("MailMind couldn't log in. Check the address, the password and the server's settings.")
