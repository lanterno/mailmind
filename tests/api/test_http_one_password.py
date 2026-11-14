"""A credential kept in 1Password, over HTTP: a server reads one through the `op` program, which no test may start, so
each read here is one the server's tripwire refuses and says so. An account whose password is a 1Password reference
asks op for it as it logs in, and a Claude API key that is one asks op for it when AI's state is read, which then
says AI is off. What op gives back, and how a read is kept for the process, are tests/unit/accounts/test_secrets.py's
and the Rust server's own tests' (core/crates/mm-server/tests/one_password.rs, mm-accounts' tests/one_password.rs).
"""
import json

import pytest

from fixtures import world as w

REF = "op://Private/Ada/password"


def refused(app):
    """What the server's tripwire refused since the last check, which a test that means to trip it takes, so the
    check as the test ends finds nothing."""
    with pytest.raises(AssertionError) as said:
        app.check()
    return json.loads(str(said.value).split("problems ", 1)[1])


def test_an_account_whose_password_is_in_1password_asks_op_for_it_as_it_logs_in(app):
    edited = app.post("/api/accounts/edit", {"email": w.ADA, "credential": {"style": "op", "ref": REF}}).json
    assert edited["account"]["credential"] == {"style": "op", "set": True, "ref": REF}
    tested = app.post("/api/accounts/test", {"email": w.ADA}).json
    assert (tested["ok"], tested["connect"], tested["email"]) == (True, False, w.ADA)
    assert set(refused(app)) == {"start op"}


def test_a_claude_api_key_in_1password_asks_op_for_it_and_ai_is_off_without_it(app, world):
    with app.stopped():
        path = world.dir / "mailmind.config.json"
        written = json.loads(path.read_text())
        written["anthropic_api_key"] = {"op": "op://Private/Claude/credential"}
        path.write_text(json.dumps(written))
    assert app.get("/api/digest").json["ai"]["engine"] == "none"
    assert set(refused(app)) == {"start op"}
