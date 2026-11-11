"""An account not yet saved or synced: testing a draft never sends a stored secret, and it can be sent from at once."""
import pytest

import problems


@pytest.mark.parametrize("credential", [
    {"style": "env", "var": "MAILMIND_TEST_DEE_PASSWORD"},
    {"style": "proton_bridge"},
    {"style": "op", "ref": "op://Private/Dee/password"}], ids=["env", "proton_bridge", "op"])
def test_hwf_testing_a_draft_never_sends_a_stored_secret(app, world, monkeypatch, credential):
    monkeypatch.setenv("MAILMIND_TEST_DEE_PASSWORD", "dee-stored-secret")
    monkeypatch.setenv("MAILMIND_PROTON_BRIDGE_PASS", "dee-stored-secret")
    world.server.add_account("dee@example.test", password="dee-stored-secret")
    draft = {"email": "dee@example.test", "type": "imap", "host": "127.0.0.1", "port": world.server.port,
             "starttls": True, "credential": credential}
    r = app.post("/api/accounts/test", {"draft": draft}).json
    assert r == problems.refused("save the account to test a stored secret; before then only a typed password is tried")
    assert world.server.commands_for(user="dee@example.test", command="LOGIN") == []


def test_hwf_send_accounts_come_from_config_before_any_sync(app, world):
    dee = "dee@example.test"
    entry = world.server.add_account(dee, password="dee-password")
    assert app.post("/api/accounts/add", {"email": dee, "type": "imap", "host": entry["host"], "port": entry["port"],
                                          "starttls": True, "credential": {"style": "keychain", "value": "dee-password"}
                                          }).json["ok"]
    d = app.get("/api/digest").json
    assert dee in d["send_accounts"] and dee not in d["accounts"]
