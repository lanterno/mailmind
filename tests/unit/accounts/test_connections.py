"""mailmind.accounts.application.connections.Connections.test, through its ConnectionProbe port and no server: a saved
account's credential goes only to its stored endpoints, whatever the request names, and a draft tries only what was typed;
the outgoing (SMTP) server is tried once the IMAP server has let the account in."""
import pytest

from mailmind.accounts.application.connections import PROBE_TIMEOUT, Connections
from mailmind.accounts.domain.credential import Typed
from mailmind.accounts.domain.conn_error import quiet
from mailmind.kernel.errors import NotFound, Refused

ADA = {"email": "ada@example.test", "kind": "imap", "host": "imap.example.test", "port": 993, "starttls": False,
       "credential": {"op": "op://Private/Ada/password"}}


class Probe:
    def __init__(self, raises=None, outgoing=None):
        self.calls, self.raises, self.outgoing_raises = [], raises, outgoing

    def _called(self, how, acct, timeout):
        self.calls.append((how, acct, timeout))
        if self.raises:
            raise self.raises

    def outgoing(self, acct, timeout):
        self.calls.append(("outgoing", acct, timeout))
        if self.outgoing_raises:
            raise self.outgoing_raises

    def saved(self, acct, timeout):
        self._called("saved", acct, timeout)

    def draft(self, acct, timeout):
        self._called("draft", acct, timeout)


def connections(probe):
    def nobody(*_):
        raise AssertionError("not asked of a test")
    return Connections(nobody, {ADA["email"]: ADA}.__getitem__, nobody, probe, quiet, nobody, nobody)


def test_a_saved_account_goes_to_its_stored_endpoint_whatever_the_request_names():
    probe = Probe()
    r = connections(probe).test({"email": ADA["email"], "host": "evil.example.test", "port": 25, "starttls": False,
                                 "credential": {"style": "keychain", "value": "typed"}})
    assert probe.calls == [("saved", ADA, PROBE_TIMEOUT), ("outgoing", ADA, PROBE_TIMEOUT)]
    assert (r["ok"], r["connect"], r["email"], r["detail"]) == (True, True, ADA["email"], "login ok")


def test_a_draft_goes_as_a_draft_with_what_was_typed():
    probe = Probe()
    r = connections(probe).test({"draft": {"email": "dee@example.test", "type": "imap", "host": "imap.example.test",
                                           "credential": {"style": "keychain", "value": "typed"}}})
    [(how, acct, timeout), (then, sent, _)] = probe.calls
    assert (how, acct["host"], acct["credential"], timeout) == ("draft", "imap.example.test", Typed("typed"), PROBE_TIMEOUT)
    assert (then, sent["smtp_host"], sent["smtp_port"], sent["credential"]) == ("outgoing", "imap.example.test", 587, Typed("typed"))
    assert (r["connect"], r["email"]) == (True, "dee@example.test")


def test_a_draft_the_domain_refuses_reaches_no_server():
    probe = Probe()
    with pytest.raises(Refused, match="save the account"):
        connections(probe).test({"draft": {"email": "dee@example.test", "type": "imap", "host": "imap.example.test",
                                           "credential": {"style": "env", "var": "HOME"}}})
    assert probe.calls == []


@pytest.mark.parametrize("port", ["nine", 70000, -1, 2 ** 64, [993]], ids=repr)
def test_a_draft_whose_port_no_connection_could_reach_says_so(port):
    probe = Probe()
    r = connections(probe).test({"draft": {"email": "dee@example.test", "host": "h.example.test", "port": port,
                                           "credential": {"style": "keychain", "value": "typed"}}})
    assert r == {"ok": True, "connect": False, "email": "dee@example.test", "error": "port must be 1–65535"}
    assert probe.calls == []


def test_a_draft_may_give_its_port_as_digits():
    probe = Probe()
    r = connections(probe).test({"draft": {"email": "dee@example.test", "host": "h.example.test", "port": "143",
                                           "credential": {"style": "keychain", "value": "typed"}}})
    assert (r["connect"], probe.calls[0][1]["port"]) == (True, 143)


@pytest.mark.parametrize("error, says", [(ValueError("invalid literal for int()"), "ValueError"),
                                         (OSError("Invalid argument"), "OSError")], ids=lambda v: str(v))
def test_what_merely_says_invalid_isnt_a_failed_login(error, says):
    assert quiet(error) == says


def test_a_refused_login_still_reads_as_one():
    assert quiet(ValueError("b'[AUTHENTICATIONFAILED] Invalid credentials'")) == "AUTHENTICATIONFAILED"


def test_an_account_no_one_saved_is_not_found():
    with pytest.raises(NotFound, match="account not found"):
        connections(Probe()).test({"email": "zed@example.test"})


@pytest.mark.parametrize("error, says", [(ConnectionRefusedError("hunter2 was refused"), "connection refused"),
                                         (TimeoutError("timed out"), "timeout")], ids=lambda v: str(v))
def test_a_failure_says_what_went_wrong_in_words_of_its_own(error, says):
    r = connections(Probe(raises=error)).test({"email": ADA["email"]})
    assert r == {"ok": True, "connect": False, "email": ADA["email"], "error": says}


class SMTPAuthenticationError(Exception):
    """As smtplib names a refused AUTH: what the words go by is the class's name and what it says."""


@pytest.mark.parametrize("raised, says", [(SMTPAuthenticationError(535, b"5.7.8 nope"), "SMTP AUTHENTICATIONFAILED"),
                                          (ConnectionRefusedError(61, "Connection refused"), "SMTP connection refused"),
                                          (TimeoutError("timed out"), "SMTP timeout")], ids=["auth", "refused", "timeout"])
def test_an_outgoing_server_that_wont_take_the_login_says_so_after_smtp(raised, says):
    probe = Probe(outgoing=raised)
    r = connections(probe).test({"email": ADA["email"]})
    assert r == {"ok": True, "connect": False, "email": ADA["email"], "error": says}
    assert [how for how, *_ in probe.calls] == ["saved", "outgoing"]


def test_an_outgoing_refusal_at_a_gmail_account_isnt_said_as_its_app_password():
    gmail = {**ADA, "kind": "gmail"}
    conn = Connections(None, {ADA["email"]: gmail}.__getitem__, None, Probe(outgoing=SMTPAuthenticationError(535)),
                       quiet, None, None)
    assert conn.test({"email": ADA["email"]})["error"] == "SMTP AUTHENTICATIONFAILED"


def test_no_outgoing_server_is_tried_once_the_imap_server_says_no():
    probe = Probe(raises=ConnectionRefusedError(61, "Connection refused"))
    assert connections(probe).test({"email": ADA["email"]})["error"] == "connection refused"
    assert [how for how, *_ in probe.calls] == ["saved"]


@pytest.mark.parametrize("port", ["nine", 70000, -1], ids=repr)
def test_a_draft_whose_outgoing_port_no_connection_could_reach_says_so(port):
    probe = Probe()
    r = connections(probe).test({"draft": {"email": "dee@example.test", "host": "h.example.test", "smtp_port": port,
                                           "credential": {"style": "keychain", "value": "typed"}}})
    assert r == {"ok": True, "connect": False, "email": "dee@example.test", "error": "SMTP port must be 1–65535"}
    assert probe.calls == []
