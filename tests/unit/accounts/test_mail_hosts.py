"""A domain's mail servers, asked of a nameserver (accounts.adapters.mail_hosts): the question as RFC 1035 writes it, the
answer read with its names' compression followed and no further than it goes, and the nameservers resolv.conf lists asked
in turn. A nameserver is the fake on loopback (fakes.dns_server, through the `dns` fixture), or bytes made here."""
import struct

import pytest

from mailmind.accounts.adapters import mail_hosts
from mailmind.accounts.adapters.mail_hosts import Failed, answer, nameservers_in, query
from mailmind.platform.db import txguard

ACME = "acme.example.test"
GOOGLE = ["aspmx.l.google.com", "alt1.aspmx.l.google.com"]


def test_resolv_conf_names_the_nameservers_in_its_order():
    text = """# a comment naming nameserver 10.0.0.9
search example.test
nameserver 192.0.2.53
nameserver   2001:db8::53
nameserver fe80::1%en0
nameserver not-an-address
nameserver
; nameserver 192.0.2.99
options ndots:2
nameserver 198.51.100.53 # the last
"""
    assert nameservers_in(text) == [("192.0.2.53", 53), ("2001:db8::53", 53), ("198.51.100.53", 53)]


def test_a_question_asks_for_the_domains_mx_records_with_recursion():
    assert query(0x1234, ACME) == (b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
                                   b"\x04acme\x07example\x04test\x00\x00\x0f\x00\x01")


@pytest.mark.parametrize("domain", ["", "a..test", "a" * 64 + ".test", "bücher.test", ".".join(["a" * 63] * 4) + ".test"])
def test_no_question_carries_what_isnt_a_domain(domain):
    with pytest.raises(ValueError):
        query(1, domain)


def name(*labels):
    return b"".join(bytes([len(label)]) + label.encode() for label in labels) + b"\0"


QUESTION = name("acme", "example", "test") + b"\x00\x0f\x00\x01"


def reply(*records, ident=7, flags=0x8180, question=QUESTION, count=None):
    head = struct.pack(">HHHHHH", ident, flags, 1, len(records) if count is None else count, 0, 0)
    return head + question + b"".join(records)


def record(owner, kind, data, klass=1):
    return owner + struct.pack(">HHIH", kind, klass, 300, len(data)) + data


def mx(preference, host):
    return struct.pack(">H", preference) + host


AT_QUESTION = b"\xc0\x0c"


def test_an_answer_names_each_mx_hosts_in_its_order():
    data = reply(record(AT_QUESTION, 15, mx(10, name("aspmx", "l", "google", "com"))),
                 record(AT_QUESTION, 15, mx(20, name("Alt1", "ASPMX", "l", "google", "com"))))
    assert answer(7, ACME, data) == GOOGLE


def test_a_name_compressed_into_one_before_it_is_read_whole():
    first = name("aspmx", "l", "google", "com")
    at_first = 12 + len(QUESTION) + 12 + 2        # the first record's host, after its owner, fields and preference
    data = reply(record(AT_QUESTION, 15, mx(10, first)),
                 record(AT_QUESTION, 15, mx(20, b"\x04alt1" + struct.pack(">H", 0xC000 | at_first))),
                 record(AT_QUESTION, 15, mx(30, b"\x04mail" + struct.pack(">H", 0xC000 | 12))))
    assert answer(7, ACME, data) == GOOGLE + ["mail.acme.example.test"]


def test_records_of_other_kinds_are_passed_over():
    data = reply(record(AT_QUESTION, 5, name("mail", "example", "test")),
                 record(AT_QUESTION, 15, mx(10, name("mx", "example", "test")), klass=3),
                 record(AT_QUESTION, 15, mx(10, name("aspmx", "l", "google", "com"))))
    assert answer(7, ACME, data) == ["aspmx.l.google.com"]


@pytest.mark.parametrize("flags", [0x8183, 0x8180])
def test_a_domain_with_no_mx_records_or_none_at_all_names_none(flags):
    assert answer(7, ACME, reply(flags=flags)) == []


@pytest.mark.parametrize("flags", [0x8182, 0x8185, 0x8184])
def test_a_nameserver_that_says_it_failed_is_passed_over(flags):
    with pytest.raises(Failed):
        answer(7, ACME, reply(flags=flags))


@pytest.mark.parametrize("data", [
    reply(ident=8),                                                            # another question's
    reply(flags=0x0100),                                                       # a question, not an answer
    reply(question=name("other", "example", "test") + b"\x00\x0f\x00\x01"),  # another domain's
    reply(question=QUESTION[:-4] + b"\x00\x01\x00\x01"),                       # another record type's
    reply(question=QUESTION[:-1]),                                             # cut short
    reply()[:11],
    b"",
])
def test_what_isnt_the_answer_to_the_question_is_not_read_as_one(data):
    assert answer(7, ACME, data) is None


def test_an_answer_cut_short_names_the_hosts_it_holds_whole():
    whole = record(AT_QUESTION, 15, mx(10, name("aspmx", "l", "google", "com")))
    second = record(AT_QUESTION, 15, mx(20, name("alt1", "aspmx", "l", "google", "com")))
    assert answer(7, ACME, reply(whole, second[:-3])) == ["aspmx.l.google.com"]
    assert answer(7, ACME, reply(whole, count=5)) == ["aspmx.l.google.com"]


@pytest.mark.parametrize("host", [
    struct.pack(">H", 0xC000 | 12 + len(QUESTION) + 12 + 2),   # a pointer to itself
    b"\x04mail\xc0",                                            # a pointer cut short
    b"\x04ma",                                                  # a label past the data
    b"\x44mail\x00",                                            # a label of a kind no answer uses
    b"\x04m\xc3\xa9l\x00",                                      # not ASCII
    b"".join(b"\x3f" + b"a" * 63 for _ in range(4)) + b"\x00",  # longer than a name can be
])
def test_a_name_that_cant_be_read_ends_the_answer(host):
    assert answer(7, ACME, reply(record(AT_QUESTION, 15, mx(10, host)))) == []


def test_a_domains_mail_servers_are_asked_of_the_nameserver(dns):
    dns.mx(ACME, GOOGLE)
    assert mail_hosts.of(ACME) == GOOGLE
    assert dns.asked == [(ACME, 15)]


def test_a_domain_no_nameserver_knows_has_none(dns):
    assert mail_hosts.of("nowhere.example.test") == []


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(mail_hosts, "TIMEOUT", 0.2)


def test_a_nameserver_silent_or_failing_is_passed_over_for_the_next(dns, quick, monkeypatch):
    from fakes.dns_server import FakeDNSServer
    dns.mx(ACME, GOOGLE)
    with FakeDNSServer() as silent, FakeDNSServer() as failing:
        silent.silent.add(ACME)
        failing.failing.add(ACME)
        monkeypatch.setattr(mail_hosts, "system_nameservers", lambda: [silent.address, failing.address, dns.address])
        assert mail_hosts.of(ACME) == GOOGLE
        assert (silent.asked, failing.asked, dns.asked) == ([(ACME, 15)], [(ACME, 15)], [(ACME, 15)])


def test_at_most_three_nameservers_are_asked(dns, quick, monkeypatch):
    from fakes.dns_server import FakeDNSServer
    dns.mx(ACME, GOOGLE)
    with FakeDNSServer() as one, FakeDNSServer() as two, FakeDNSServer() as three:
        for silent in (one, two, three):
            silent.silent.add(ACME)
        monkeypatch.setattr(mail_hosts, "system_nameservers", lambda: [one.address, two.address, three.address, dns.address])
        assert mail_hosts.of(ACME) == []
        assert dns.asked == []


def test_no_nameserver_no_lookup(monkeypatch):
    monkeypatch.setattr(mail_hosts, "system_nameservers", lambda: [])
    assert mail_hosts.of(ACME) == []


def test_a_lookup_is_never_made_with_a_transaction_open(dns, monkeypatch):
    monkeypatch.setattr(txguard, "open_transactions", lambda: 1)
    with pytest.raises(txguard.TxAcrossNetworkError, match="a DNS lookup"):
        mail_hosts.of(ACME)
    assert dns.asked == []
