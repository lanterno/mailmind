"""The loopback TLS pin (mailmind.accounts.domain.tls_pin, accounts.check_pin): on loopback, where
Proton Bridge's certificate can't be verified, the first certificate an account meets is pinned,
and any other after it is refused before a password is sent. Every world account is on loopback
behind STARTTLS, as Bridge is."""
import json
import time

import pytest

import problems
from fixtures import world as w
from mailmind.accounts.domain import tls_pin
from mailmind.bootstrap import mailbox

WRONG = "sha256:" + "0" * 64


def cfg(world):
    with open(world.dir / "mailmind.config.json") as f:
        return json.load(f)


def pins(world, email=w.ADA):
    return next(a for a in cfg(world)["accounts"] if a["email"] == email).get("tls_pins")


def pin(world, email, where, fingerprint):
    """Pin `fingerprint` for `email` at `where`, as a certificate met before would have."""
    from mailmind.bootstrap.accounts import config
    raw = config._raw()
    entry = next(a for a in raw["accounts"] if a["email"] == email)
    entry.setdefault("tls_pins", {})[where] = fingerprint
    config.save(raw)


def imap(world):
    return f"127.0.0.1:{world.server.port}"


def connect(email=w.ADA):
    from mailmind.bootstrap import accounts
    M = accounts.connect(accounts.get(email), timeout=10)
    M.logout()


def logins(world, email=w.ADA):
    return len(world.server.commands_for(email, "LOGIN"))


def wait_until(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def test_the_first_connection_pins_the_certificate_and_the_next_must_match_it(world):
    assert pins(world) is None
    connect()
    assert pins(world) == {imap(world): "sha256:" + world.server.cert_sha256}
    connect()
    assert logins(world) == 2


def test_a_changed_certificate_is_refused_before_the_password(world):
    pin(world, w.ADA, imap(world), WRONG)
    with pytest.raises(tls_pin.TLSPinMismatch):
        connect()
    assert logins(world) == 0
    assert pins(world) == {imap(world): WRONG}                 # the pin stands until trusted again


def test_each_account_keeps_its_own_pins(world):
    pin(world, w.ADA, imap(world), WRONG)
    connect(w.BEA)                                             # the same server, another account
    assert pins(world, w.BEA) == {imap(world): "sha256:" + world.server.cert_sha256}


def test_off_this_machine_nothing_is_pinned(world):
    from mailmind.bootstrap import accounts
    before = cfg(world)
    accounts.check_pin(accounts.get(w.ADA), "imap.example.test", 993, object())   # never reads a socket
    assert cfg(world) == before


def test_a_draft_for_a_new_address_pins_nothing(app, world):
    entry = world.server.add_account("dee@example.test")
    before = cfg(world)
    r = app.post("/api/accounts/test", {"draft": {"email": "dee@example.test", "type": "imap", "host": entry["host"],
                                                  "port": entry["port"], "starttls": True,
                                                  "credential": {"style": "plain", "value": "app-password"}}})
    assert r.json["connect"] is True
    assert cfg(world) == before


def test_testing_an_account_whose_certificate_changed_says_so(app, world):
    pin(world, w.ADA, imap(world), WRONG)
    r = app.post("/api/accounts/test", {"email": w.ADA}).json
    assert (r["connect"], r["error"]) == (False, "certificate changed")
    assert logins(world) == 0


def test_a_changed_certificate_shows_in_health_and_trusting_it_again_syncs(app, world):
    pin(world, w.ADA, imap(world), WRONG)
    mailbox.syncing.sync_accounts({w.ADA})
    health = {a["account"]: a for a in app.get("/api/account-health").json["accounts"]}
    assert (health[w.ADA]["last_error_kind"], health[w.ADA]["last_error"]) == ("TLS_PIN", "TLSPinMismatch")
    r = app.post("/api/accounts/trust-cert", {"email": w.ADA})
    assert (r.status, r.json) == (200, {"ok": True, "account": w.ADA})
    assert wait_until(lambda: pins(world) == {imap(world): "sha256:" + world.server.cert_sha256})

    def healthy():
        return {a["account"]: a for a in app.get("/api/account-health").json["accounts"]}[w.ADA]["healthy"]
    assert wait_until(healthy)


def test_trusting_an_unknown_account_is_refused(app, world):
    r = app.post("/api/accounts/trust-cert", {"email": "zed@example.test"})
    assert (r.status, r.json) == (404, problems.not_found("not found"))


def test_a_move_while_the_certificate_is_wrong_waits_in_the_queue(app, world):
    pin(world, w.ADA, imap(world), WRONG)
    r = app.post("/api/archive", {**world.copy("ada-inbox-05-receipt.eml"), "mutation_id": "m-1"}).json
    assert r["status"] == "queued"
    assert logins(world) == 0


def test_smtp_pins_its_own_endpoint_and_refuses_a_changed_certificate(world):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.compose import transport
    smtp = f"127.0.0.1:{world.smtp.port}"
    transport.connect(accounts.get(w.ADA)).quit()
    assert pins(world)[smtp] == "sha256:" + world.smtp.cert_sha256
    pin(world, w.ADA, smtp, WRONG)
    auths = len([c for c in world.smtp.commands if c["command"] == "AUTH"])
    with pytest.raises(tls_pin.TLSPinMismatch):
        transport.connect(accounts.get(w.ADA))
    assert len([c for c in world.smtp.commands if c["command"] == "AUTH"]) == auths


def test_bridge_set_to_ssl_connects_and_is_pinned(world):
    """Bridge can serve IMAP over implicit TLS too. Its certificate is as self-signed there."""
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.accounts import config
    from fakes.imap_server import FakeIMAPServer
    with FakeIMAPServer(kind="proton", implicit_tls=True) as srv:
        entry = srv.add_account("dee@example.test")
        config.add_account({**entry, "credential": {"style": "plain", "value": "app-password"}})
        M = accounts.connect(accounts.get("dee@example.test"), timeout=10)
        M.logout()
        assert pins(world, "dee@example.test") == {f"127.0.0.1:{srv.port}": "sha256:" + srv.cert_sha256}
