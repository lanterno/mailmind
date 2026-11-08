"""Accounts' routes on their router: what the schemas refuse, what they leave to the use case's own words, what never gets out."""
import json
import logging
import pathlib

import pytest

import problems
import schema_contract
from fixtures import world as w
from mailmind.accounts.domain.draft import throwaway_account
from mailmind.bootstrap import accounts, assistant
from mailmind.http.schemas.accounts import AccountDraft
from mailmind.platform import logging as logs
from mailmind.platform import settings

BAD_REQUEST = problems.BAD_REQUEST
SECRET = "hunter2-the-typed-password"
TYPED = {"style": "plain", "value": SECRET}
NEW = "dee@example.test"


def config(world):
    return json.loads((world.dir / "mailmind.config.json").read_text())


def dee(world):
    """A draft that would log in, with the password that fake server takes."""
    entry = world.server.add_account(NEW, password="dee-password")
    return {"email": NEW, "type": "imap", "host": entry["host"], "port": entry["port"], "starttls": True,
            "credential": {"style": "plain", "value": "dee-password"}}


# ── what the use case still words ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field, junk, said", [
    ("port", "x", "port must be 1–65535"),
    ("port", [1], "port must be 1–65535"),
    ("port", 70000, "port must be 1–65535"),
    ("smtp_port", {"a": 1}, "SMTP port must be 1–65535"),
    ("smtp_port", 0, "SMTP port must be 1–65535"),
    ("host", 123, "host must be a host name"),
    ("host", "two words", "host must be a host name"),
    ("smtp_host", [1], "SMTP host must be a host name"),
    ("type", 5, "type must be gmail, imap, or proton"),
    ("type", "pop3", "type must be gmail, imap, or proton"),
    ("credential", SECRET, "a credential names its style"),
    ("credential", [1], "a credential names its style"),
    ("credential", {"style": "plain"}, "password required"),
    ("credential", {"style": "plain", "value": 123}, "password required"),
    ("credential", {"style": "op", "ref": 123}, "1Password reference required"),
    ("credential", {"style": "env", "var": 123}, "environment variable name required"),
])
def test_what_accounts_words_for_itself_it_still_gets_to_say(app, world, field, junk, said):
    before = config(world)
    added = app.post("/api/accounts/add", {"email": NEW, "type": "imap", "host": "mail.example.test", "credential": TYPED, field: junk})
    assert (added.status, added.json) == (400, problems.refused(said))
    edited = app.post("/api/accounts/edit", {"email": w.ADA, field: junk})
    assert (edited.status, edited.json) == (400, problems.refused(said))
    assert config(world) == before


@pytest.mark.parametrize("body, said", [
    ({"type": "imap", "host": "mail.example.test", "credential": TYPED}, "a valid email is required"),
    ({"email": NEW, "type": "imap", "host": "mail.example.test"}, "a credential is required"),
    ({"email": NEW, "type": "imap", "credential": TYPED}, "a host is required for a generic IMAP account"),
    ({"email": w.ADA, "type": "imap", "host": "mail.example.test", "credential": TYPED}, f"{w.ADA} already exists"),
])
def test_what_an_add_goes_without_is_the_use_cases_to_word(app, body, said):
    r = app.post("/api/accounts/add", body)
    assert (r.status, r.json) == (400, problems.refused(said))


def test_a_draft_that_isnt_one_is_still_a_failed_test_and_not_a_failed_request(app, world):
    r = app.post("/api/accounts/test", {"draft": {**dee(world), "port": "nine"}})
    assert (r.status, r.json["ok"], r.json["connect"], r.json["email"]) == (200, True, False, NEW)


@pytest.mark.parametrize("credential", [{"style": "env", "var": "HOME"}, {"style": "proton_bridge"}, {"style": "op", "ref": "op://V/I/F"}])
def test_a_draft_naming_a_stored_secret_is_still_refused_in_its_own_words(app, world, credential):
    r = app.post("/api/accounts/test", {"draft": {**dee(world), "credential": credential}})
    assert (r.status, r.json["type"]) == (400, "/problems/refused")
    assert "save the account" in r.json["detail"]
    assert world.server.commands_for(user=NEW, command="LOGIN") == []


# ── what a schema refuses ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path, body", [
    ("/api/accounts/test", {"email": 5}),
    ("/api/accounts/test", {"draft": 0}),
    ("/api/accounts/test", {"draft": []}),
    ("/api/accounts/test", {"draft": {"email": 5}}),
    ("/api/accounts/test", {"draft": {"starttls": "no"}}),
    ("/api/accounts/test", {"draft": {"strip_spaces": 1}}),
    ("/api/accounts/trust-cert", {"email": [w.ADA]}),
    ("/api/accounts/delete", {"email": 5}),
    ("/api/accounts/edit", {"email": w.ADA, "alias": 5}),
    ("/api/accounts/edit", {"email": w.ADA, "signature": [1]}),
    ("/api/accounts/edit", {"email": w.ADA, "trash": 5}),
    ("/api/accounts/edit", {"email": w.ADA, "starttls": "yes"}),
    ("/api/accounts/edit", {"email": w.ADA, "smtp_starttls": 1}),
    ("/api/accounts/edit", {"email": w.ADA, "strip_spaces": "no"}),
    ("/api/accounts/edit", {"email": w.ADA, "disabled": "no"}),
    ("/api/accounts/add", {"email": NEW, "alias": {"a": 1}}),
])
def test_a_value_its_model_refuses_is_the_400_and_changes_nothing(app, world, path, body):
    before = config(world)
    r = app.post(path, body)
    assert (r.status, r.json) == (400, BAD_REQUEST)
    assert config(world) == before


def test_a_field_nothing_here_reads_is_nothing_to_refuse(app, world):
    r = app.post("/api/accounts/edit", {"email": w.ADA, "alias": "Ada L.", "thread_id": 5, "subject": [1], "tls_pins": "x"})
    assert (r.status, r.json["account"]["alias"]) == (200, "Ada L.")
    assert "tls_pins" not in next(a for a in config(world)["accounts"] if a["email"] == w.ADA)


def test_what_an_edit_leaves_out_stays_as_it_was(app, world):
    before = next(a for a in config(world)["accounts"] if a["email"] == w.ADA)
    assert app.post("/api/accounts/edit", {"email": w.ADA, "signature": "Ada"}).status == 200
    after = next(a for a in config(world)["accounts"] if a["email"] == w.ADA)
    assert after == {**before, "signature": "Ada"}


def test_a_flag_that_says_nothing_changes_nothing(app, world):
    before = next(a for a in config(world)["accounts"] if a["email"] == w.ADA)
    assert app.post("/api/accounts/edit", {"email": w.ADA, "starttls": None, "disabled": None}).status == 200
    assert next(a for a in config(world)["accounts"] if a["email"] == w.ADA) == before


def test_a_draft_reads_nothing_its_model_doesnt_name(monkeypatch):
    """The contract watches what a use case reads of a request, and a draft is one level in."""
    schema_contract.watch_reads(monkeypatch)
    draft = AccountDraft(email=NEW, type="imap", host="mail.example.test", port=993, starttls=True, strip_spaces=False, credential=TYPED)
    assert throwaway_account(draft.model_dump(exclude_unset=True))["credential"] == {"plain": SECRET}
    schema_contract.verify()


# ── where a credential goes ──────────────────────────────────────────────────────────────────────────

def test_a_saved_account_goes_to_its_saved_server_with_its_saved_password_whatever_the_request_names(app, world):
    """The tripwire would fail this on any host but the fake's, and the fake takes only the saved password."""
    r = app.post("/api/accounts/test", {"email": w.ADA, "host": "evil.example.test", "port": 25, "starttls": False,
                                        "credential": {"style": "plain", "value": "wrong"}, "draft": {}})
    assert (r.status, r.json["connect"], r.json["email"]) == (200, True, w.ADA)


def test_a_draft_tries_what_was_typed_and_keeps_none_of_it(app, world):
    before = config(world)
    r = app.post("/api/accounts/test", {"draft": dee(world)})
    assert (r.status, r.json["ok"], r.json["connect"], r.json["detail"]) == (200, True, True, "login ok")
    assert config(world) == before


# ── a password never gets out ────────────────────────────────────────────────────────────────────────

def in_the_log(capsys):
    """What reached the terminal and the file since the log started."""
    in_the_file = pathlib.Path(settings.current().home) / "mailmind.log"
    return capsys.readouterr().out + (in_the_file.read_text() if in_the_file.exists() else "")


def said(capsys, caplog):
    """That, and every record as it was logged, ahead of what withholds: pytest's handler hears them raw, a refusal's included."""
    return in_the_log(capsys) + caplog.text


REFUSED_WITH_A_PASSWORD = [
    ("/api/accounts/add", {"email": 5, "credential": TYPED}),
    ("/api/accounts/add", {"email": NEW, "alias": [SECRET], "credential": TYPED}),
    ("/api/accounts/add", {"email": NEW, "credential": SECRET}),
    ("/api/accounts/add", {"email": "not-an-address", "type": "imap", "host": "mail.example.test", "credential": TYPED}),
    ("/api/accounts/add", {"email": NEW, "type": "imap", "port": "x", "credential": TYPED}),
    ("/api/accounts/edit", {"email": w.ADA, "starttls": "yes", "credential": TYPED}),
    ("/api/accounts/edit", {"email": w.ADA, "signature": [SECRET], "credential": TYPED}),
    ("/api/accounts/edit", {"email": 5, "credential": TYPED}),
    ("/api/accounts/test", {"draft": SECRET}),
    ("/api/accounts/test", {"draft": [SECRET]}),
    ("/api/accounts/test", {"draft": {"email": 5, "credential": TYPED}}),
    ("/api/accounts/test", {"draft": {"email": NEW, "starttls": "no", "credential": TYPED}}),
    ("/api/accounts/test", {"email": [SECRET]}),
    ("/api/accounts/trust-cert", {"email": {"value": SECRET}}),
    ("/api/accounts/delete", {"email": [SECRET]}),
]


@pytest.fixture(params=[None, "debug"], ids=["usual", "debug"])
def logging_all_it_may(request, app, monkeypatch, capsys, caplog):
    if request.param:
        monkeypatch.setenv("MAILMIND_LOG_LEVEL", request.param)
    logs.setup()
    caplog.set_level(logging.DEBUG)
    capsys.readouterr()


@pytest.mark.parametrize("path, body", REFUSED_WITH_A_PASSWORD)
def test_a_refused_bodys_password_is_in_neither_the_answer_nor_the_log(app, logging_all_it_may, capsys, caplog, path, body):
    r = app.post(path, body)
    assert r.status == 400
    assert SECRET not in json.dumps(r.json)
    assert SECRET not in said(capsys, caplog)


def test_a_body_that_wont_parse_gets_the_400_and_is_said_nowhere(app, logging_all_it_may, capsys, caplog):
    r = app.post("/api/accounts/add", f'{{"email": "{NEW}", "credential": {{"value": "{SECRET}"'.encode())
    assert (r.status, r.json) == (400, BAD_REQUEST)
    assert SECRET not in said(capsys, caplog)


def test_a_failed_login_says_nothing_of_what_was_typed(app, world, logging_all_it_may, capsys, caplog):
    draft = {**dee(world), "credential": TYPED}
    wrong = app.post("/api/accounts/test", {"draft": draft})
    assert (wrong.status, wrong.json["connect"], wrong.json["error"]) == (200, False, "AUTHENTICATIONFAILED")
    down = app.post("/api/accounts/test", {"draft": {**draft, "port": 9}})
    assert (down.status, down.json["connect"]) == (200, False)
    assert SECRET not in json.dumps([wrong.json, down.json])
    assert SECRET not in said(capsys, caplog)


def test_a_use_case_that_raises_with_a_password_in_its_text_answers_the_fixed_500(app, monkeypatch, capsys):
    logs.setup()
    capsys.readouterr()

    def boom(*a):
        raise RuntimeError(f"could not log in with {SECRET}")
    monkeypatch.setattr(accounts.connections, "test", boom)
    r = app.post("/api/accounts/test", {"email": w.ADA})
    assert (r.status, r.json) == (500, problems.FAILED)
    assert SECRET not in in_the_log(capsys)


# ── an answer never holds a secret ───────────────────────────────────────────────────────────────────

def test_every_style_of_credential_is_listed_by_its_mask(app):
    """Disabled, so no sync goes looking for any of them."""
    for address, style in (("op@example.test", {"style": "op", "ref": "op://V/I/F"}), ("ops@example.test", {"style": "op", "ref": ["op://V/I/A", "op://V/I/B"]}),
                           ("env@example.test", {"style": "env", "var": "DEE_PASSWORD"}), ("bridge@example.test", {"style": "proton_bridge"})):
        added = app.post("/api/accounts/add", {"email": address, "type": "gmail", "disabled": True, "credential": style})
        assert added.status == 200
    r = app.get("/api/accounts")
    shown = {row["email"]: row["credential"] for row in r.json["accounts"]}
    assert shown["op@example.test"] == {"style": "op", "set": True, "ref": "op://V/I/F"}
    assert shown["ops@example.test"] == {"style": "op", "set": True, "ref": ["op://V/I/A", "op://V/I/B"]}
    assert shown["env@example.test"] == {"style": "env", "set": True, "var": "DEE_PASSWORD"}
    assert shown["bridge@example.test"] == {"style": "proton_bridge", "set": True}
    assert shown[w.ADA] == {"style": "plain", "set": True}
    assert "app-password" not in json.dumps(r.json)


def test_what_a_row_answered_with_a_secret_would_fail_the_contract(app):
    """So the suite would see a route that started answering one."""
    route = schema_contract.route_for("GET", "/api/accounts")
    good = app.get("/api/accounts").json
    assert schema_contract.fault_in(route, 200, good) is None
    row = good["accounts"][0]
    for leak in ({**row, "password": SECRET}, {**row, "credential": {**row["credential"], "value": SECRET}},
                 {**row, "credential": {**row["credential"], "plain": SECRET}}, {**row, "tls_pins": {"a": "sha256:0"}}):
        assert schema_contract.fault_in(route, 200, {**good, "accounts": [leak]}) is not None


# ── an action that isn't one ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/api/accounts/no-such-action", "/api/accounts/", "/api/accounts/test/", "/api/accounts/x/test", "/api/accounts"])
def test_what_isnt_an_action_answers_not_found(app, world, path):
    logins = len(world.server.commands_for(user=w.ADA, command="LOGIN"))
    r = app.post(path, {"email": w.ADA})
    assert (r.status, r.json, r.headers["content-type"]) == (404, problems.NO_ROUTE, problems.CONTENT_TYPE)
    assert len(world.server.commands_for(user=w.ADA, command="LOGIN")) == logins


@pytest.mark.parametrize("path", ["/api/accounts/test", "/api/accounts/add", "/api/accounts/edit", "/api/accounts/delete", "/api/accounts/trust-cert"])
def test_an_action_answers_only_a_post(app, path):
    r = app.get(path)
    assert (r.status, r.json) == (404, problems.NO_ROUTE)


def test_an_action_that_isnt_one_is_held_to_the_perimeter_all_the_same(app):
    assert app.post("/api/accounts/no-such-action", {}, headers={"X-CSRF-Token": "wrong"}).status == 403


# ── who counts as someone here ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ask", [lambda app: app.get("/api/accounts"), lambda app: app.get("/api/account-health"),
                                 lambda app: app.post("/api/accounts/test", {"email": "zed@example.test"}),
                                 lambda app: app.post("/api/accounts/edit", {"email": "zed@example.test"}),
                                 lambda app: app.post("/api/accounts/delete", {"email": "zed@example.test"}),
                                 lambda app: app.post("/api/accounts/trust-cert", {"email": "zed@example.test"}),
                                 lambda app: app.post("/api/accounts/add", {}),
                                 lambda app: app.post("/api/accounts/no-such-action", {})],
                         ids=["list", "health", "test", "edit", "delete", "trust-cert", "add", "unknown-action"])
def test_asking_counts_as_someone_here(app, monkeypatch, ask):
    seen = []
    monkeypatch.setattr(assistant.governor(), "saw_someone", lambda: seen.append(1))
    ask(app)
    assert seen == [1]


def test_a_refused_request_counts_as_no_one(app, monkeypatch):
    seen = []
    monkeypatch.setattr(assistant.governor(), "saw_someone", lambda: seen.append(1))
    assert app.post("/api/accounts/edit", {"email": w.ADA}, headers={"X-CSRF-Token": "wrong"}).status == 403
    assert app.post("/api/accounts/edit", {"email": 5}).status == 400
    assert seen == []
