"""What a failed test of a login says when Gmail refused it (conn_error.quiet): Gmail's answer to LOGIN says which refusal
it is, and each comes to a sentence that says what to do, never what the server said. The refusals come as either IMAP
adapter raises them (imap_connection.login_failed); the route's answer is tests/api/test_http_gmail_refusals.py's."""
import imaplib

import pytest

from mailmind.accounts.adapters.imap_connection import login_failed
from mailmind.accounts.application.connections import Connections
from mailmind.accounts.domain.conn_error import APP_PASSWORD, GMAIL_REFUSED, IMAP_OFF, SAID, WEB_SIGN_IN, quiet, refusal

# Gmail's answers, as it gives them.
NEEDS_APP_PASSWORD = "[ALERT] Application-specific password required: https://support.google.com/accounts/answer/185833 (Failure)"
WEB_BROWSER = "[ALERT] Please log in via your web browser: https://support.google.com/mail/accounts/answer/78754 (Failure)"
WEB_LOGIN = "[WEBALERT https://accounts.google.com/ContinueSignIn?sarp=1&scc=1] Web login required."
DOMAIN_OFF = ("[ALERT] IMAP access is disabled for your domain. Please contact your domain administrator for questions "
              "about this feature. (Failure)")
ACCOUNT_OFF = ("[ALERT] Your account is not enabled for IMAP use. Please visit your Gmail settings page and enable your "
               "account for IMAP access. (Failure)")
INVALID = "[AUTHENTICATIONFAILED] Invalid credentials (Failure)"


def refused(said):
    """What a LOGIN Gmail answered NO comes to, as imaplib raises it."""
    return login_failed(imaplib.IMAP4.error(said.encode()))


@pytest.mark.parametrize("said, kind", [(NEEDS_APP_PASSWORD, APP_PASSWORD), (WEB_BROWSER, APP_PASSWORD),
                                        (WEB_LOGIN, WEB_SIGN_IN), (DOMAIN_OFF, IMAP_OFF), (ACCOUNT_OFF, IMAP_OFF),
                                        (NEEDS_APP_PASSWORD.lower(), APP_PASSWORD), (INVALID, None),
                                        ("[UNAVAILABLE] try again later", None), ("", None)])
def test_gmails_answer_says_which_refusal_it_is(said, kind):
    assert refusal(said) == kind


@pytest.mark.parametrize("said, kind", [(NEEDS_APP_PASSWORD, APP_PASSWORD), (WEB_BROWSER, APP_PASSWORD),
                                        (WEB_LOGIN, WEB_SIGN_IN), (DOMAIN_OFF, IMAP_OFF), (ACCOUNT_OFF, IMAP_OFF)])
@pytest.mark.parametrize("account", ["gmail", "imap", None])
def test_each_of_gmails_refusals_is_said_plainly_whatever_the_account_says_it_is(said, kind, account):
    assert quiet(refused(said), account) == SAID[kind]


def test_a_wrong_app_password_at_a_gmail_account_is_said_as_one():
    assert quiet(refused(INVALID), "gmail") == GMAIL_REFUSED
    assert quiet(refused("[AUTHENTICATIONFAILED] Authentication failed."), "gmail") == GMAIL_REFUSED


@pytest.mark.parametrize("account", ["imap", "proton", None])
def test_another_accounts_refusal_says_what_it_always_said(account):
    assert quiet(refused(INVALID), account) == "AUTHENTICATIONFAILED"


def test_what_isnt_a_refusal_at_a_gmail_account_isnt_said_as_one():
    assert quiet(refused("[UNAVAILABLE] try again later"), "gmail") == "AUTHENTICATIONFAILED"
    assert quiet(RuntimeError("no credential configured for cy@example.test"), "gmail") == "AUTHENTICATIONFAILED"
    assert quiet(TimeoutError("timed out"), "gmail") == "timeout"


def test_no_sentence_holds_what_the_server_said():
    for said in [NEEDS_APP_PASSWORD, WEB_LOGIN, DOMAIN_OFF, f"{INVALID} hunter2"]:
        words = quiet(refused(said + " hunter2"), "gmail")
        assert "hunter2" not in words and "https://" not in words and "(Failure)" not in words


class Probe:
    def __init__(self, raises):
        self.raises = raises

    def saved(self, acct, timeout):
        raise self.raises

    def draft(self, acct, timeout):
        raise self.raises


def connections(raises, kind):
    saved = {"email": "cy@example.test", "kind": kind, "host": "imap.example.test", "port": 993, "starttls": False,
             "credential": {"keychain": "account:cy@example.test"}}

    def nobody(*_):
        raise AssertionError("not asked of a test")
    return Connections(nobody, {saved["email"]: saved}.__getitem__, nobody, Probe(raises), quiet, nobody, nobody)


@pytest.mark.parametrize("kind, says", [("gmail", GMAIL_REFUSED), ("imap", "AUTHENTICATIONFAILED")])
def test_a_test_of_a_saved_account_says_it_as_its_type_has_it(kind, says):
    assert connections(refused(INVALID), kind).test({"email": "cy@example.test"})["error"] == says
    assert connections(refused(NEEDS_APP_PASSWORD), kind).test({"email": "cy@example.test"})["error"] == SAID[APP_PASSWORD]


@pytest.mark.parametrize("kind, says", [("gmail", GMAIL_REFUSED), ("imap", "AUTHENTICATIONFAILED")])
def test_a_test_of_a_draft_says_it_as_its_type_has_it(kind, says):
    draft = {"email": "dee@example.test", "type": kind, "host": "imap.example.test",
             "credential": {"style": "keychain", "value": "abcd efgh ijkl mnop"}}
    assert connections(refused(INVALID), kind).test({"draft": draft})["error"] == says
