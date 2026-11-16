"""The preset an address's account takes, over HTTP (GET /api/accounts/preset), as Settings asks while the address is
typed: a provider's for its own domains (Gmail's, iCloud's, Yahoo's, Microsoft's that MailMind can't log in to yet...),
offered where the domain's MX records name a provider's mail servers, none for any other. The server asks the test's fake nameserver (the `dns` fixture), and no other: without it, none at all. Which
domains and hosts count is tests/unit/accounts/test_presets.py's, and the lookup's test_mail_hosts.py's."""
from urllib.parse import quote

import pytest

import problems

ACME = "acme.example.test"


def preset(app, email):
    return app.get(f"/api/accounts/preset?email={quote(email)}")


def answer(domain, found, sure):
    return {"ok": True, "domain": domain, "preset": found, "sure": sure}


def which(r):
    """What an answer says of which preset it is: the rest is what that preset says, which the tests below hold."""
    return {k: r.json[k] for k in ("ok", "domain", "preset", "sure")}


NOTHING = {"name": None, "supported": True, "settings": None, "note": None, "link": None}


@pytest.mark.parametrize("email, domain", [("ada@gmail.com", "gmail.com"), ("Ada@GoogleMail.com", "googlemail.com")])
def test_an_address_at_googles_own_domain_takes_gmails_preset_with_no_lookup(app, dns, email, domain):
    r = preset(app, email)
    assert (r.status, which(r)) == (200, answer(domain, "gmail", True))
    assert r.json["settings"]["type"] == "gmail"
    assert dns.asked == []


def test_a_domain_whose_mail_googles_servers_take_is_offered_gmails_preset(app, dns):
    dns.mx(ACME, ["aspmx.l.google.com", "alt1.aspmx.l.google.com", "alt2.aspmx.l.google.com"])
    r = preset(app, "ada@Acme.Example.Test")
    assert (r.status, which(r)) == (200, answer(ACME, "gmail", False))
    assert dns.asked == [(ACME, 15)]


@pytest.mark.parametrize("hosts", [["mx1.example.test", "mx2.example.test"], [], None],
                         ids=["other-hosts", "no-hosts", "no-domain"])
def test_any_other_domain_is_offered_nothing(app, dns, hosts):
    if hosts is not None:                   # None: the domain doesn't exist
        dns.mx(ACME, hosts)
    assert preset(app, f"ada@{ACME}").json == answer(ACME, None, False) | NOTHING


def test_a_nameserver_that_fails_offers_nothing(app, dns):
    dns.failing.add(ACME)
    assert preset(app, f"ada@{ACME}").json == answer(ACME, None, False) | NOTHING


@pytest.mark.parametrize("email", ["ada", "ada@", "ada@localhost", "ada@bücher.example.test", "ada@exam ple.test"],
                         ids=["no-at", "nothing-after", "one-label", "not-ascii", "a-space"])
def test_an_address_with_no_domain_a_lookup_could_ask_about_asks_none(app, dns, email):
    assert preset(app, email).json == answer(None, None, False) | NOTHING
    assert dns.asked == []


@pytest.mark.parametrize("path", ["/api/accounts/preset", "/api/accounts/preset?email=", "/api/accounts/preset?email=%20"],
                         ids=["none", "blank", "a-space"])
def test_no_address_at_all_is_a_bad_request(app, path):
    r = app.get(path)
    assert (r.status, r.json) == (400, problems.BAD_REQUEST)


def test_with_no_nameserver_named_none_is_asked(app):
    assert preset(app, f"ada@{ACME}").json == answer(ACME, None, False) | NOTHING


def test_icloud_takes_its_servers_its_trash_and_where_its_app_specific_password_is_made(app, dns):
    r = preset(app, "ada@me.com")
    assert (r.status, r.json) == (200, {
        "ok": True, "domain": "me.com", "preset": "icloud", "sure": True, "name": "iCloud", "supported": True,
        "settings": {"type": "imap", "host": "imap.mail.me.com", "port": 993, "starttls": False,
                     "smtp_host": "smtp.mail.me.com", "smtp_port": 587, "smtp_starttls": True, "trash": "Deleted Messages"},
        "note": "iCloud Mail takes an app-specific password, not your Apple Account password: make one at account.apple.com, "
                "under Sign-In and Security, then paste it here.",
        "link": {"url": "https://account.apple.com", "words": "Make an app-specific password"}})
    assert dns.asked == []


@pytest.mark.parametrize("email, found, smtp", [
    ("ada@yahoo.co.uk", "yahoo", "smtp.mail.yahoo.com"), ("ada@aol.com", "aol", "smtp.aol.com"),
    ("ada@fastmail.fm", "fastmail", "smtp.fastmail.com"),
], ids=["yahoo", "aol", "fastmail"])
def test_a_provider_that_sends_over_tls_from_the_first_byte_says_so(app, dns, email, found, smtp):
    r = preset(app, email)
    assert (r.json["preset"], r.json["sure"], r.json["supported"]) == (found, True, True)
    assert (r.json["settings"]["smtp_host"], r.json["settings"]["smtp_port"], r.json["settings"]["smtp_starttls"]) == (smtp, 465, False)
    assert "app password" in r.json["note"]


def test_yahoo_japan_is_no_yahoo_address_and_is_looked_up_as_any_other(app, dns):
    """Yahoo Japan is another company's, with servers of its own: its domain takes no preset of its own, and its MX
    records name no provider's servers MailMind knows."""
    dns.mx("yahoo.co.jp", ["mx.mail.yahoo.co.jp"])
    assert preset(app, "ada@yahoo.co.jp").json == answer("yahoo.co.jp", None, False) | NOTHING
    assert dns.asked == [("yahoo.co.jp", 15)]


@pytest.mark.parametrize("email", ["ada@outlook.com", "ada@hotmail.fr", "ada@live.com"])
def test_microsofts_addresses_are_named_with_nothing_to_save(app, dns, email):
    r = preset(app, email)
    assert (r.json["preset"], r.json["name"], r.json["supported"], r.json["settings"]) == ("microsoft", "Microsoft", False, None)
    assert "doesn't support yet" in r.json["note"]


def test_proton_says_bridge_is_needed_and_where_it_is_got(app, dns):
    r = preset(app, "ada@proton.me")
    assert (r.json["preset"], r.json["settings"]["type"]) == ("proton", "proton")
    assert r.json["link"] == {"url": "https://proton.me/mail/bridge", "words": "Get Proton Bridge"}


@pytest.mark.parametrize("mx, found", [("in1-smtp.messagingengine.com", "fastmail"), ("mx01.mail.icloud.com", "icloud"),
                                       ("acme-example-test.mail.protection.outlook.com", "microsoft")],
                         ids=["fastmail", "icloud-custom-domain", "microsoft-365"])
def test_a_domain_whose_mail_another_providers_servers_take_is_offered_its_preset(app, dns, mx, found):
    dns.mx(ACME, [mx])
    assert which(preset(app, f"ada@{ACME}")) == answer(ACME, found, False)
