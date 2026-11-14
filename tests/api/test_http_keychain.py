"""What a person keeps in the login Keychain from Settings, over HTTP: the Claude API key, which mailmind.config.json names
where the Assistant reads it, and the Proton Bridge's password, which a proton_bridge account logs in with. Each is kept
with a POST and taken out with another, in the tests' stand-in for the Keychain, which the server shares; no answer
holds the secret, and each says only whether one is set.

Where the Bridge's password is looked for, and that what is found is kept for the process, are
tests/unit/accounts/test_secrets.py's and the Rust server's own tests' (mm-accounts' tests/bridge.rs).
"""
import json

import pytest

import problems

KEY = "sk-ant-api03-kept-from-settings"
OTHER_KEY = "sk-ant-api03-kept-again"
BRIDGE = "bridge-password-0"
PIA = "pia@example.test"            # not one of the world's accounts
UNPRINTABLE = ("A secret MailMind keeps in the Keychain is printable ASCII, and not empty: letters, digits, punctuation and "
               "spaces.")


def config(world):
    return json.loads((world.dir / "mailmind.config.json").read_text())


def says_nothing_of(answer, *secrets):
    text = json.dumps(answer.body) if isinstance(answer.body, (dict, list)) else repr(answer.body)
    return not any(secret in text for secret in secrets)


def test_nothing_is_set_to_begin_with(app):
    assert app.get("/api/ai/key").json == {"ok": True, "set": False}
    assert app.get("/api/accounts/bridge-password").json == {"ok": True, "set": False}


def test_the_claude_api_key_is_kept_in_the_keychain_and_the_config_names_it(app, world, fake_keychain):
    assert app.get("/api/digest").json["ai"]["engine"] == "none"
    kept = app.post("/api/ai/key", {"key": KEY})
    assert (kept.status, kept.json) == (200, {"ok": True, "set": True})
    assert fake_keychain.items()["anthropic-api-key"] == KEY
    assert config(world)["anthropic_api_key"] == {"keychain": "anthropic-api-key", "version": 1}
    assert app.get("/api/ai/key").json == {"ok": True, "set": True}
    ai = app.get("/api/digest").json["ai"]
    assert (ai["engine"], ai["key_from"]) == ("api", "mailmind.config.json")

    # Another takes its place, under a new version, so a process that read the first reads the Keychain again.
    assert app.post("/api/ai/key", {"key": OTHER_KEY}).json == {"ok": True, "set": True}
    assert fake_keychain.items()["anthropic-api-key"] == OTHER_KEY
    assert config(world)["anthropic_api_key"] == {"keychain": "anthropic-api-key", "version": 2}

    cleared = app.post("/api/ai/key/clear", {})
    assert (cleared.status, cleared.json) == (200, {"ok": True, "set": False})
    assert "anthropic-api-key" not in fake_keychain.items() and "anthropic_api_key" not in config(world)
    assert app.get("/api/ai/key").json == {"ok": True, "set": False}
    assert app.get("/api/digest").json["ai"]["engine"] == "none"
    assert app.post("/api/ai/key/clear", {}).json == {"ok": True, "set": False}


def test_clearing_the_key_takes_out_whatever_the_config_named(app, world):
    with app.stopped():
        written = config(world)
        written["anthropic_api_key"] = {"env": "A_KEY_OF_MY_OWN"}
        (world.dir / "mailmind.config.json").write_text(json.dumps(written))
    assert app.get("/api/ai/key").json == {"ok": True, "set": True}
    assert app.post("/api/ai/key/clear", {}).json == {"ok": True, "set": False}
    assert "anthropic_api_key" not in config(world)


@pytest.mark.parametrize("path, field", [("/api/ai/key", "key"), ("/api/accounts/bridge-password", "password")])
def test_a_secret_the_keychain_cant_hold_is_refused_in_its_words_and_nothing_is_kept(app, world, fake_keychain, path,
                                                                                      field):
    before = (config(world), fake_keychain.items())
    for typed in ["", "sk-ant-café", "two\nlines"]:
        refused = app.post(path, {field: typed})
        assert (refused.status, refused.json) == (400, problems.refused(UNPRINTABLE)), typed
    for body in [{}, {field: None}, {field: 7}, {field: ["x"]}]:
        refused = app.post(path, body)
        assert (refused.status, refused.json) == (400, problems.BAD_REQUEST), body
    assert (config(world), fake_keychain.items()) == before


def test_the_bridges_password_is_kept_and_a_proton_account_logs_in_with_it(app, world, fake_keychain):
    entry = world.server.add_account(PIA, password=BRIDGE, kind="proton")
    added = app.post("/api/accounts/add", {"email": PIA, "type": "proton", "host": entry["host"], "port": entry["port"],
                                           "starttls": entry["starttls"], "credential": {"style": "proton_bridge"}})
    assert added.json["account"]["credential"] == {"style": "proton_bridge", "set": True}
    tested = app.post("/api/accounts/test", {"email": PIA}).json
    assert (tested["connect"], tested["error"]) == (False, "AUTHENTICATIONFAILED")

    kept = app.post("/api/accounts/bridge-password", {"password": BRIDGE})
    assert (kept.status, kept.json) == (200, {"ok": True, "set": True}) and says_nothing_of(kept, BRIDGE)
    assert fake_keychain.items()["proton-bridge-password"] == BRIDGE
    assert BRIDGE not in (world.dir / "mailmind.config.json").read_text()
    assert app.get("/api/accounts/bridge-password").json == {"ok": True, "set": True}
    assert app.post("/api/accounts/test", {"email": PIA}).json["connect"] is True

    # Taken out, the password found before is let go too: the next login looks again, and finds none.
    cleared = app.post("/api/accounts/bridge-password/clear", {})
    assert (cleared.status, cleared.json) == (200, {"ok": True, "set": False})
    assert "proton-bridge-password" not in fake_keychain.items()
    assert app.get("/api/accounts/bridge-password").json == {"ok": True, "set": False}
    assert app.post("/api/accounts/test", {"email": PIA}).json["connect"] is False
    assert app.post("/api/accounts/bridge-password/clear", {}).json == {"ok": True, "set": False}


def test_a_new_bridge_password_takes_the_place_of_the_one_a_login_found(app, world, fake_keychain):
    entry = world.server.add_account(PIA, password=BRIDGE, kind="proton")
    app.post("/api/accounts/add", {"email": PIA, "type": "proton", "host": entry["host"], "port": entry["port"],
                                   "starttls": entry["starttls"], "credential": {"style": "proton_bridge"}})
    app.post("/api/accounts/bridge-password", {"password": "an-old-one"})
    assert app.post("/api/accounts/test", {"email": PIA}).json["connect"] is False
    app.post("/api/accounts/bridge-password", {"password": BRIDGE})
    assert app.post("/api/accounts/test", {"email": PIA}).json["connect"] is True
    assert fake_keychain.items()["proton-bridge-password"] == BRIDGE
