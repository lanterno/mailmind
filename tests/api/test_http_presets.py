"""The preset an address's account takes, over HTTP (GET /api/accounts/preset), as Settings asks while the address is
typed: Gmail's for Google's own domains, offered where the domain's MX records name Google's mail servers, none for any
other. The server asks the test's fake nameserver (the `dns` fixture), and no other: without it, none at all. Which
domains and hosts count is tests/unit/accounts/test_presets.py's, and the lookup's test_mail_hosts.py's."""
from urllib.parse import quote

import pytest

import problems

ACME = "acme.example.test"


def preset(app, email):
    return app.get(f"/api/accounts/preset?email={quote(email)}")


def answer(domain, found, sure):
    return {"ok": True, "domain": domain, "preset": found, "sure": sure}


@pytest.mark.parametrize("email, domain", [("ada@gmail.com", "gmail.com"), ("Ada@GoogleMail.com", "googlemail.com")])
def test_an_address_at_googles_own_domain_takes_gmails_preset_with_no_lookup(app, dns, email, domain):
    r = preset(app, email)
    assert (r.status, r.json) == (200, answer(domain, "gmail", True))
    assert dns.asked == []


def test_a_domain_whose_mail_googles_servers_take_is_offered_gmails_preset(app, dns):
    dns.mx(ACME, ["aspmx.l.google.com", "alt1.aspmx.l.google.com", "alt2.aspmx.l.google.com"])
    r = preset(app, "ada@Acme.Example.Test")
    assert (r.status, r.json) == (200, answer(ACME, "gmail", False))
    assert dns.asked == [(ACME, 15)]


@pytest.mark.parametrize("hosts", [["mx1.example.test", "mx2.example.test"], [], None],
                         ids=["other-hosts", "no-hosts", "no-domain"])
def test_any_other_domain_is_offered_nothing(app, dns, hosts):
    if hosts is not None:                   # None: the domain doesn't exist
        dns.mx(ACME, hosts)
    assert preset(app, f"ada@{ACME}").json == answer(ACME, None, False)


def test_a_nameserver_that_fails_offers_nothing(app, dns):
    dns.failing.add(ACME)
    assert preset(app, f"ada@{ACME}").json == answer(ACME, None, False)


@pytest.mark.parametrize("email", ["ada", "ada@", "ada@localhost", "ada@bücher.example.test", "ada@exam ple.test"],
                         ids=["no-at", "nothing-after", "one-label", "not-ascii", "a-space"])
def test_an_address_with_no_domain_a_lookup_could_ask_about_asks_none(app, dns, email):
    assert preset(app, email).json == answer(None, None, False)
    assert dns.asked == []


@pytest.mark.parametrize("path", ["/api/accounts/preset", "/api/accounts/preset?email=", "/api/accounts/preset?email=%20"],
                         ids=["none", "blank", "a-space"])
def test_no_address_at_all_is_a_bad_request(app, path):
    r = app.get(path)
    assert (r.status, r.json) == (400, problems.BAD_REQUEST)


def test_with_no_nameserver_named_none_is_asked(app):
    assert preset(app, f"ada@{ACME}").json == answer(ACME, None, False)
