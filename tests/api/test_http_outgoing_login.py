"""A test of an account's login over HTTP (POST /api/accounts/test) logs in at its outgoing (SMTP) server too, once its IMAP
server has let it in, so an account that can read mail but not send says so before a send fails. What the outgoing server's
trouble comes to is said after "SMTP ", in the words the IMAP server's would be. The world's accounts send through its fake
SMTP server."""
import socket

from fixtures import world as w

REFUSED = "535 5.7.8 authentication credentials invalid"


def quit_by(world, who):
    """The QUITs sent from a session logged in as `who`: one for each login at the outgoing server that worked."""
    return [c for c in world.smtp.commands_for("QUIT") if c["user"] == who]


def ada_draft(world, **smtp):
    """Ada's account as typed, with the password both fake servers take for her."""
    return {"email": w.ADA, "type": "imap", "host": "127.0.0.1", "port": world.server.port,
            "starttls": not world.server.implicit_tls, "credential": {"style": "keychain", "value": w.PASSWORDS[w.ADA]}, **smtp}


def nothing_listens():
    """A loopback port nothing listens on, which refuses a connection."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_saved_accounts_test_logs_in_at_its_outgoing_server_too(app, world):
    r = app.post("/api/accounts/test", {"email": w.ADA})
    assert (r.status, r.json["connect"]) == (200, True)
    assert len(quit_by(world, w.ADA)) == 1


def test_an_outgoing_server_that_refuses_the_login_says_so(app, world):
    world.smtp.fail("AUTH", REFUSED, times=None)
    r = app.post("/api/accounts/test", {"email": w.ADA})
    assert (r.status, r.json["connect"], r.json["email"], r.json["error"]) == (200, False, w.ADA, "SMTP AUTHENTICATIONFAILED")


def test_a_gmail_accounts_outgoing_refusal_isnt_said_as_a_wrong_app_password(app, world):
    world.smtp.fail("AUTH", REFUSED, times=None)
    assert app.post("/api/accounts/test", {"email": w.CY}).json["error"] == "SMTP AUTHENTICATIONFAILED"


def test_a_draft_tries_the_outgoing_server_typed_with_it(app, world):
    r = app.post("/api/accounts/test", {"draft": ada_draft(world, smtp_host="127.0.0.1", smtp_port=world.smtp.port)})
    assert r.json["connect"] is True
    assert len(quit_by(world, w.ADA)) == 1


def test_an_outgoing_server_that_isnt_there_says_so(app, world):
    r = app.post("/api/accounts/test", {"draft": ada_draft(world, smtp_host="127.0.0.1", smtp_port=nothing_listens())})
    assert (r.json["connect"], r.json["error"]) == (False, "SMTP connection refused")


def test_no_outgoing_server_is_tried_once_the_imap_server_says_no(app, world):
    world.server.fail("LOGIN", "NO [AUTHENTICATIONFAILED] Invalid credentials (Failure)", user=w.ADA)
    with world.smtp.lock:
        before = len(world.smtp.commands)
    assert app.post("/api/accounts/test", {"email": w.ADA}).json["error"] == "AUTHENTICATIONFAILED"
    with world.smtp.lock:
        assert world.smtp.commands[before:] == []
