"""A Gmail App Password typed into Settings as Google shows it, four groups of four letters with spaces between, over HTTP:
the server keeps it in the Keychain without its spaces, and a test of it, saved or not, logs in with it so. Any other
account keeps a password as it was typed. Which passwords are taken for one is tests/unit/accounts/test_app_passwords.py's.
"""
import json

DEE = "dee@example.test"            # not one of the world's accounts
APP_PASSWORD = "abcdefghijklmnop"
SHOWN = "abcd efgh ijkl mnop"


def typed(password):
    return {"style": "keychain", "value": password}


def at(entry):
    """The fake server's endpoint for an account it holds, in place of the one its type would give."""
    return {"host": entry["host"], "port": entry["port"], "starttls": entry["starttls"]}


def test_a_gmail_app_password_as_google_shows_it_is_kept_without_its_spaces_and_logs_in(app, world, fake_keychain):
    entry = world.server.add_account(DEE, password=APP_PASSWORD, kind="gmail")
    fake_keychain.delete(f"account:{DEE}")      # what the fake server's own entry kept
    draft = {"email": DEE, "type": "gmail", **at(entry), "credential": typed(SHOWN)}
    assert app.post("/api/accounts/test", {"draft": draft}).json["connect"] is True

    added = app.post("/api/accounts/add", draft)
    assert added.status == 200 and SHOWN not in json.dumps(added.json) and APP_PASSWORD not in json.dumps(added.json)
    assert fake_keychain.find(f"account:{DEE}") == APP_PASSWORD
    assert app.post("/api/accounts/test", {"email": DEE}).json["connect"] is True

    app.post("/api/accounts/edit", {"email": DEE, "credential": typed("qrst uvwx yzab cdef")})
    assert fake_keychain.find(f"account:{DEE}") == "qrstuvwxyzabcdef"


def test_another_accounts_password_is_kept_as_it_was_typed(app, world, fake_keychain):
    entry = world.server.add_account(DEE, password=SHOWN)
    fake_keychain.delete(f"account:{DEE}")
    app.post("/api/accounts/add", {"email": DEE, "type": "imap", **at(entry), "credential": typed(SHOWN)})
    assert fake_keychain.find(f"account:{DEE}") == SHOWN
    assert app.post("/api/accounts/test", {"email": DEE}).json["connect"] is True


def test_a_gmail_password_that_isnt_an_app_password_is_kept_as_it_was_typed(app, world, fake_keychain):
    app.post("/api/accounts/add", {"email": DEE, "type": "gmail", "credential": typed("my own password")})
    assert fake_keychain.find(f"account:{DEE}") == "my own password"
