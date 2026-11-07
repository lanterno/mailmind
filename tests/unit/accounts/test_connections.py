"""mailmind.accounts.application.connections.Connections.test, through its ConnectionProbe port and no server: a saved
account's credential goes only to its stored endpoint, whatever the request names, and a draft tries only what was typed."""
import pytest

from mailmind.accounts.application.connections import PROBE_TIMEOUT, Connections
from mailmind.accounts.domain.conn_error import quiet

ADA = {"email": "ada@example.test", "kind": "imap", "host": "imap.example.test", "port": 993, "starttls": False,
       "credential": {"op": "op://Private/Ada/password"}}


class Probe:
    def __init__(self, raises=None):
        self.calls, self.raises = [], raises

    def _called(self, how, acct, timeout):
        self.calls.append((how, acct, timeout))
        if self.raises:
            raise self.raises

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
                                 "credential": {"style": "plain", "value": "typed"}})
    assert probe.calls == [("saved", ADA, PROBE_TIMEOUT)]
    assert (r["ok"], r["connect"], r["email"], r["detail"]) == (True, True, ADA["email"], "login ok")


def test_a_draft_goes_as_a_draft_with_what_was_typed():
    probe = Probe()
    r = connections(probe).test({"draft": {"email": "dee@example.test", "type": "imap", "host": "imap.example.test",
                                           "credential": {"style": "plain", "value": "typed"}}})
    [(how, acct, timeout)] = probe.calls
    assert (how, acct["host"], acct["credential"], timeout) == ("draft", "imap.example.test", {"plain": "typed"}, PROBE_TIMEOUT)
    assert (r["connect"], r["email"]) == (True, "dee@example.test")


def test_a_draft_the_domain_refuses_reaches_no_server():
    probe = Probe()
    r = connections(probe).test({"draft": {"email": "dee@example.test", "type": "imap", "host": "imap.example.test",
                                           "credential": {"style": "env", "var": "HOME"}}})
    assert probe.calls == [] and r["ok"] is False and "save the account" in r["error"]


def test_a_draft_that_isnt_one_is_a_failed_test_rather_than_a_failed_request():
    probe = Probe()
    assert connections(probe).test({"draft": {"email": "dee@example.test", "host": "h.example.test", "port": "nine"}})["connect"] is False
    assert probe.calls == []


def test_an_account_no_one_saved_is_not_found():
    assert connections(Probe()).test({"email": "zed@example.test"}) == {"ok": False, "error": "account not found"}


@pytest.mark.parametrize("error, says", [(ConnectionRefusedError("hunter2 was refused"), "connection refused"),
                                         (TimeoutError("timed out"), "timeout")], ids=lambda v: str(v))
def test_a_failure_says_what_went_wrong_in_words_of_its_own(error, says):
    r = connections(Probe(raises=error)).test({"email": ADA["email"]})
    assert r == {"ok": True, "connect": False, "email": ADA["email"], "error": says}
