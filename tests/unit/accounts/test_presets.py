"""Which preset an address's account takes (domain.preset, application.presets): a provider's own domains take its preset,
a domain whose MX records name a provider's mail servers is offered it, and any other takes none, through the MailHosts port
and no network. The lookup itself is test_mail_hosts.py's, and the route's answer tests/api/test_http_presets.py's."""
import pytest

from mailmind.accounts.application.presets import Presets
from mailmind.accounts.domain.preset import domain_of, names_google, provider_of_host
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
    """What an answer says of which preset it is: the rest is what that preset says, which the tests below hold."""
    return {"ok": True, "domain": domain, "preset": preset, "sure": sure}


def which(said):
    return {k: said[k] for k in ("ok", "domain", "preset", "sure")}


def preset_of(email):
    return Presets(Hosts({})).preset(email)


@pytest.mark.parametrize("email", ["ada@gmail.com", "ada@googlemail.com", "Ada@GMAIL.com."])
def test_an_address_at_googles_own_domain_takes_gmails_preset_with_no_lookup(email):
    hosts = Hosts({})
    assert which(Presets(hosts).preset(email)) == answer(domain_of(email), "gmail", True)
    assert hosts.asked == []


def test_a_domain_whose_mail_googles_servers_take_is_offered_gmails_preset():
    hosts = Hosts({"acme.example.test": ["mx.example.test", "aspmx.l.google.com"]})
    assert which(Presets(hosts).preset("ada@Acme.Example.Test")) == answer("acme.example.test", "gmail", False)
    assert hosts.asked == ["acme.example.test"]


@pytest.mark.parametrize("found", [[], ["mx.example.test"], [""], ["google.com.example.test"]])
def test_any_other_domain_is_offered_nothing(found):
    assert Presets(Hosts({"acme.example.test": found})).preset("ada@acme.example.test") == {
        "ok": True, "domain": "acme.example.test", "preset": None, "sure": False, "name": None, "supported": True,
        "settings": None, "note": None, "link": None}


@pytest.mark.parametrize("email", ["ada", "ada@localhost", "ada@bücher.example.test"])
def test_an_address_with_no_domain_a_lookup_could_ask_about_asks_none(email):
    hosts = Hosts({})
    assert which(Presets(hosts).preset(email)) == answer(None, None, False)
    assert hosts.asked == []


@pytest.mark.parametrize("email", [None, "", "   "])
def test_no_address_at_all_is_a_bad_request(email):
    with pytest.raises(Refused, match="bad request"):
        Presets(Hosts({})).preset(email)


@pytest.mark.parametrize("email, found", [
    ("ada@icloud.com", "icloud"), ("ada@me.com", "icloud"), ("ada@mac.com", "icloud"),
    ("ada@yahoo.com", "yahoo"), ("ada@yahoo.co.uk", "yahoo"), ("ada@yahoo.fr", "yahoo"), ("ada@yahoo.com.au", "yahoo"),
    ("ada@yahoo.co.in", "yahoo"), ("ada@ymail.com", "yahoo"),
    ("ada@aol.com", "aol"), ("ada@aol.de", "aol"), ("ada@fastmail.com", "fastmail"), ("ada@fastmail.fm", "fastmail"),
    ("ada@outlook.com", "microsoft"), ("ada@hotmail.com", "microsoft"), ("ada@hotmail.co.uk", "microsoft"),
    ("ada@live.com", "microsoft"), ("ada@live.fr", "microsoft"), ("ada@msn.com", "microsoft"),
    ("ada@proton.me", "proton"), ("ada@protonmail.com", "proton"), ("ada@pm.me", "proton"),
])
def test_a_providers_own_domains_take_its_preset_with_no_lookup(email, found):
    hosts = Hosts({})
    said = Presets(hosts).preset(email)
    assert (said["preset"], said["sure"]) == (found, True)
    assert hosts.asked == []


@pytest.mark.parametrize("email", ["ada@live.example.test", "ada@yahoo.example.test", "ada@mail.yahoo.com", "ada@notyahoo.com",
                                   "ada@aol.org", "ada@live.net", "ada@yahoo.co.u2", "ada@outlook.com.example"])
def test_a_brand_under_a_domain_that_isnt_a_countrys_ending_is_no_providers(email):
    assert preset_of(email)["preset"] is None


@pytest.mark.parametrize("email", ["ada@yahoo.co.jp", "ada@yahoo.ne.jp", "ada@yahoo.jp"])
def test_yahoo_japan_is_another_companys_with_servers_of_its_own(email):
    hosts = Hosts({})
    said = Presets(hosts).preset(email)
    assert (said["preset"], said["sure"]) == (None, False)
    assert len(hosts.asked) == 1                    # looked up as any other domain
    assert preset_of("ada@hotmail.co.jp")["preset"] == "microsoft"


def test_icloud_says_its_servers_its_trash_and_where_its_app_specific_password_is_made():
    assert preset_of("ada@icloud.com") == {
        "ok": True, "domain": "icloud.com", "preset": "icloud", "sure": True, "name": "iCloud", "supported": True,
        "settings": {"type": "imap", "host": "imap.mail.me.com", "port": 993, "starttls": False,
                     "smtp_host": "smtp.mail.me.com", "smtp_port": 587, "smtp_starttls": True, "trash": "Deleted Messages"},
        "note": "iCloud Mail takes an app-specific password, not your Apple Account password: make one at account.apple.com, "
                "under Sign-In and Security, then paste it here.",
        "link": {"url": "https://account.apple.com", "words": "Make an app-specific password"},
    }


@pytest.mark.parametrize("email, imap, smtp", [
    ("ada@yahoo.com", "imap.mail.yahoo.com", "smtp.mail.yahoo.com"),
    ("ada@aol.com", "imap.aol.com", "smtp.aol.com"),
    ("ada@fastmail.com", "imap.fastmail.com", "smtp.fastmail.com"),
])
def test_yahoo_aol_and_fastmail_send_over_tls_from_the_first_byte_and_ask_for_an_app_password(email, imap, smtp):
    said = preset_of(email)
    assert said["settings"] == {"type": "imap", "host": imap, "port": 993, "starttls": False, "smtp_host": smtp,
                                "smtp_port": 465, "smtp_starttls": False, "trash": None}
    assert "app password" in said["note"] and said["link"]["url"].startswith("https://")


def test_gmail_and_proton_take_their_types_whose_defaults_give_the_servers():
    gmail, proton = preset_of("ada@gmail.com"), preset_of("ada@proton.me")
    assert (gmail["settings"]["type"], gmail["settings"]["host"]) == ("gmail", None)
    assert gmail["link"]["url"] == "https://myaccount.google.com/apppasswords"
    assert proton["settings"]["type"] == "proton"
    assert proton["link"] == {"url": "https://proton.me/mail/bridge", "words": "Get Proton Bridge"}
    assert all(w in proton["note"] for w in ("Proton Bridge", "paid", "running"))


def test_microsoft_is_named_and_unsupported_with_nothing_to_save():
    said = preset_of("ada@outlook.com")
    assert (said["name"], said["supported"], said["settings"], said["link"]) == ("Microsoft", False, None, None)
    assert "doesn't support yet" in said["note"]


@pytest.mark.parametrize("mx, found", [
    ("in1-smtp.messagingengine.com", "fastmail"), ("mx01.mail.icloud.com", "icloud"),
    ("acme-example-test.mail.protection.outlook.com", "microsoft"), ("mail.protonmail.ch", "proton"), ("ASPMX.L.GOOGLE.COM.", "gmail"),
])
def test_a_domain_whose_mail_a_providers_servers_take_is_offered_its_preset(mx, found):
    hosts = Hosts({"acme.example.test": ["mx.example.test", mx]})
    assert which(Presets(hosts).preset("ada@acme.example.test")) == answer("acme.example.test", found, False)


def test_a_presets_imap_host_names_its_provider():
    assert provider_of_host("IMAP.mail.me.com").preset == "icloud"
    assert provider_of_host("imap.fastmail.com").preset == "fastmail"
    assert provider_of_host("imap.example.test") is None
