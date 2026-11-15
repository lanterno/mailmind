"""A connection's trouble in words that are safe to show: what went wrong, never what the server or the error said.

Gmail's answer to a LOGIN it refuses says which refusal it is, and each of those comes to a sentence that says what to
do (`refusal`): an App Password needed where a Google password was typed, which is also all an account with Advanced
Protection, or one whose Workspace admin has switched App Passwords off, hears; a sign-in Google wants made on the web
first; IMAP switched off. A wrong App Password at a Gmail account is said as one (`GMAIL_REFUSED`). The Rust server's
`mm_imap` reads the same words into the same classes, and `mm_accounts::connections::quiet` says the same sentences.
"""
from mailmind.accounts.domain import tls_pin
from mailmind.accounts.domain.draft import BadPort

# What Gmail's answer to LOGIN says, in its own words, and the class each refusal comes to.
APP_PASSWORD = "LoginNeedsAppPassword"
WEB_SIGN_IN = "LoginNeedsWebSignIn"
IMAP_OFF = "LoginImapOff"
_GOOGLE_SAYS = ((APP_PASSWORD, ("application-specific password", "log in via your web browser")),
                (WEB_SIGN_IN, ("[webalert", "web login required")),
                (IMAP_OFF, ("imap access is disabled", "not enabled for imap")))

SAID = {
    APP_PASSWORD: "Gmail needs an App Password here, not your Google password. If Google offers none, Advanced Protection "
                  "or a Workspace admin has switched them off.",
    WEB_SIGN_IN: "Google wants you to sign in on the web first: sign in to Gmail in your browser, then test again.",
    IMAP_OFF: "IMAP is switched off for this account: turn it on in Gmail's settings, or ask your Workspace admin to.",
}
GMAIL_REFUSED = "Gmail didn't accept that App Password: paste it again, or create a new one."


def refusal(said: str) -> str | None:
    """Which of Gmail's refusals a server's answer to LOGIN names, by its class; None for any other answer."""
    low = said.lower()
    return next((kind for kind, words in _GOOGLE_SAYS if any(w in low for w in words)), None)


def quiet(e: BaseException, kind: str | None = None) -> str:
    """`e` in words that are safe to show. `kind` is the account's type, where it is known: a login refused at a Gmail
    account says so of its App Password."""
    if isinstance(e, tls_pin.TLSPinMismatch):
        return "certificate changed"
    if isinstance(e, BadPort):
        return str(e)
    s = f"{type(e).__name__}: {e}".lower()
    google = refusal(s)
    if google:
        return SAID[google]
    if any(w in s for w in ("auth", "credential", "login", "password")):
        # `refused`: the server itself said no (LoginUnreachable's), which a credential that couldn't be had isn't
        return GMAIL_REFUSED if kind == "gmail" and getattr(e, "refused", False) else "AUTHENTICATIONFAILED"
    if "refused" in s:
        return "connection refused"
    if "timed out" in s or "timeout" in s:
        return "timeout"
    if any(w in s for w in ("name or service", "nodename", "getaddrinfo", "not known")):
        return "host not found"
    return type(e).__name__
