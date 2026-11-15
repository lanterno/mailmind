"""Which preset an address's account takes (domain.preset, application.presets): Gmail's for Google's own domains, offered
for a domain whose MX records name Google's mail servers, none for any other, through the MailHosts port and no network.
The lookup itself is test_mail_hosts.py's, and the route's answer tests/api/test_http_presets.py's."""
import pytest

from mailmind.accounts.application.presets import Presets
from mailmind.accounts.domain.preset import domain_of, names_google
from mailmind.kernel.errors import Refused


@pytest.mark.parametrize("email, domain", [
    ("ada@gmail.com", "gmail.com"),
    ("  Ada@GMail.COM  ", "gmail.com"),
    ("ada@example.test.", "example.test"),
    ("ada@mail.acme-1.example.test", "mail.acme-1.example.test"),
    ("\"a@b\"@example.test", "example.test"),
    ("ada@" + ".".join(["a" * 63] * 3) + ".test", ".".join(["a" * 63] * 3) + ".test"),
    ("ada", None),
    ("ada@", None),
    ("@", None),
    ("ada@localhost", None),
    ("ada@example..test", None),
    ("ada@.example.test", None),
    ("ada@-acme.example.test", None),
    ("ada@acme-.example.test", None),
    ("ada@acme_mail.example.test", None),
    ("ada@" + "a" * 64 + ".test", None),
    ("ada@" + ".".join(["a" * 63] * 4) + ".test", None),
    ("ada@bücher.example.test", None),
    ("ada@\u212aacme.example.test", None),       # the Kelvin sign, which lowers to an ASCII k
    ("ada@exam ple.test", None),
    ("ada@example.test/x", None),
])
def test_an_address_names_a_domain_a_lookup_could_ask_about_or_none(email, domain):
    assert domain_of(email) == domain


@pytest.mark.parametrize("host, google", [
    ("aspmx.l.google.com", True), ("ALT1.ASPMX.L.GOOGLE.COM.", True), ("smtp.google.com", True), ("google.com", True),
    ("gmail-smtp-in.l.google.com", True), ("mx.googlemail.com", True),
    ("google.com.example.test", False), ("notgoogle.com", False), ("aspmx.l.google.co", False), ("mx.example.test", False),
    ("", False),
])
def test_an_mx_host_is_googles_when_it_is_one_of_googles_mail_servers(host, google):
    assert names_google(host) is google


class Hosts:
    def __init__(self, table):
        self.table, self.asked = table, []

    def of(self, domain):
        self.asked.append(domain)
        return self.table.get(domain, [])


def answer(domain, preset, sure):
    return {"ok": True, "domain": domain, "preset": preset, "sure": sure}


@pytest.mark.parametrize("email", ["ada@gmail.com", "ada@googlemail.com", "Ada@GMAIL.com."])
def test_an_address_at_googles_own_domain_takes_gmails_preset_with_no_lookup(email):
    hosts = Hosts({})
    assert Presets(hosts).preset(email) == answer(domain_of(email), "gmail", True)
    assert hosts.asked == []


def test_a_domain_whose_mail_googles_servers_take_is_offered_gmails_preset():
    hosts = Hosts({"acme.example.test": ["mx.example.test", "aspmx.l.google.com"]})
    assert Presets(hosts).preset("ada@Acme.Example.Test") == answer("acme.example.test", "gmail", False)
    assert hosts.asked == ["acme.example.test"]


@pytest.mark.parametrize("found", [[], ["mx.example.test"], [""], ["google.com.example.test"]])
def test_any_other_domain_is_offered_nothing(found):
    assert Presets(Hosts({"acme.example.test": found})).preset("ada@acme.example.test") == answer("acme.example.test", None, False)


@pytest.mark.parametrize("email", ["ada", "ada@localhost", "ada@bücher.example.test"])
def test_an_address_with_no_domain_a_lookup_could_ask_about_asks_none(email):
    hosts = Hosts({})
    assert Presets(hosts).preset(email) == answer(None, None, False)
    assert hosts.asked == []


@pytest.mark.parametrize("email", [None, "", "   "])
def test_no_address_at_all_is_a_bad_request(email):
    with pytest.raises(Refused, match="bad request"):
        Presets(Hosts({})).preset(email)
