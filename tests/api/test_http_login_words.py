"""A test of a login that didn't work, over HTTP (POST /api/accounts/test), says what went wrong in plain words and what to
do next (`says`), with the page that helps where one does (`link`); its class stays in `error`, for the owner. Which words
each trouble takes is tests/unit/accounts/test_login_words.py's; here both servers say them of a real login."""
import socket

from fixtures import world as w

REFUSED = "535 5.7.8 authentication credentials invalid"


def nothing_listens():
    """A loopback port nothing listens on, which refuses a connection."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def draft(world, email, password="wrong", **fields):
    return {"email": email, "type": "imap", "host": "127.0.0.1", "port": world.server.port,
            "starttls": not world.server.implicit_tls, "smtp_host": "127.0.0.1", "smtp_port": world.smtp.port,
            "credential": {"style": "keychain", "value": password}, **fields}


def test_a_refused_password_at_icloud_says_to_make_an_app_specific_password_and_where(app, world):
    r = app.post("/api/accounts/test", {"draft": draft(world, "dee@icloud.com")}).json
    assert (r["connect"], r["error"]) == (False, "AUTHENTICATIONFAILED")
    assert r["says"].startswith("iCloud didn't accept that password.")
    assert r["link"] == {"url": "https://account.apple.com", "words": "Make an app-specific password"}


def test_a_refused_password_anywhere_else_says_to_check_it(app, world):
    r = app.post("/api/accounts/test", {"draft": draft(world, "dee@example.test")}).json
    assert r["says"].startswith("The server didn't accept that address and password.") and r["link"] is None


def test_a_bridge_that_isnt_running_says_so(app, world):
    proton = draft(world, "pia@proton.me", type="proton", port=nothing_listens())
    r = app.post("/api/accounts/test", {"draft": proton}).json
    assert (r["connect"], r["error"]) == (False, "connection refused")
    assert r["says"].startswith("Proton Bridge isn't running.")
    assert r["link"] == {"url": "https://proton.me/mail/bridge", "words": "Get Proton Bridge"}


def test_a_server_that_didnt_answer_says_to_check_its_name_and_port(app, world):
    r = app.post("/api/accounts/test", {"draft": draft(world, "dee@example.test", port=nothing_listens())}).json
    assert r["error"] == "connection refused" and "Check the server's name and port" in r["says"]


def test_an_outgoing_server_that_refuses_says_reading_mail_works(app, world):
    world.smtp.fail("AUTH", REFUSED, times=None)
    r = app.post("/api/accounts/test", {"email": w.ADA}).json
    assert r["error"] == "SMTP AUTHENTICATIONFAILED"
    assert r["says"].startswith("Reading mail works, but the outgoing server didn't accept the password.")


def test_a_wrong_gmail_app_password_sends_to_googles_page(app, world):
    world.server.fail("LOGIN", "NO [AUTHENTICATIONFAILED] Invalid credentials (Failure)", user=w.CY)
    r = app.post("/api/accounts/test", {"email": w.CY}).json
    assert r["says"] == r["error"] == "Gmail didn't accept that App Password: paste it again, or create a new one."
    assert r["link"] == {"url": "https://myaccount.google.com/apppasswords", "words": "Make an App Password"}
