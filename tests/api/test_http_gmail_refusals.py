"""What a test of an account's login says over HTTP when Gmail refused it: Gmail's answer to LOGIN says which refusal it
is, and the answer says what to do about it in a sentence of its own, never what the server said. Cy's is the world's
Gmail account; Ada's is generic IMAP. Which of Gmail's words come to which refusal is
tests/unit/accounts/test_gmail_refusals.py's."""
import json

import pytest

from fixtures import world as w

APP_PASSWORD = ("Gmail needs an App Password here, not your Google password. If Google offers none, Advanced Protection or "
                "a Workspace admin has switched them off.")
WEB_SIGN_IN = "Google wants you to sign in on the web first: sign in to Gmail in your browser, then test again."
IMAP_OFF = "IMAP is switched off for this account: turn it on in Gmail's settings, or ask your Workspace admin to."
WRONG = "Gmail didn't accept that App Password: paste it again, or create a new one."


@pytest.mark.parametrize("said, says", [
    ("NO [ALERT] Application-specific password required: https://support.google.com/accounts/answer/185833 (Failure)",
     APP_PASSWORD),
    ("NO [ALERT] Please log in via your web browser: https://support.google.com/mail/accounts/answer/78754 (Failure)",
     APP_PASSWORD),
    ("NO [WEBALERT https://accounts.google.com/ContinueSignIn?sarp=1&scc=1] Web login required.", WEB_SIGN_IN),
    ("NO [ALERT] IMAP access is disabled for your domain. Please contact your domain administrator for questions about "
     "this feature. (Failure)", IMAP_OFF),
    ("NO [AUTHENTICATIONFAILED] Invalid credentials (Failure)", WRONG),
], ids=["app-password", "web-browser", "web-login", "imap-off", "invalid"])
def test_gmails_refusal_is_said_plainly(app, world, said, says):
    world.server.fail("LOGIN", said, user=w.CY)
    tested = app.post("/api/accounts/test", {"email": w.CY})
    assert (tested.status, tested.json["connect"], tested.json["error"], tested.json["says"]) == (200, False, says, says)
    assert "support.google.com" not in json.dumps(tested.json) and "ContinueSignIn" not in json.dumps(tested.json)


def test_another_accounts_refusal_says_what_it_always_said(app, world):
    world.server.fail("LOGIN", "NO [AUTHENTICATIONFAILED] Invalid credentials (Failure)", user=w.ADA)
    assert app.post("/api/accounts/test", {"email": w.ADA}).json["error"] == "AUTHENTICATIONFAILED"
