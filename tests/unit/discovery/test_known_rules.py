"""Who counts as known, with no database: what is asked after, in which forms, and how the answer reads."""
import json

import pytest

from mailmind.discovery.domain import known


# -- the address mail would go to ------------------------------------------------

@pytest.mark.parametrize("raw, a", [
    ("Lin@Example.test", "lin@example.test"),
    ("Lin Ma <lin@example.test>", "lin@example.test"),
    ('"bea@example.test" <lin@example.test>', "lin@example.test"),      # a name holding an address stands in for nothing
])
def test_it_is_the_one_mail_would_go_to(raw, a):
    assert known.normal(raw) == a


@pytest.mark.parametrize("raw", ["", None, "nobody", "lin@example.test, bea@example.test", "a@b@example.test"])
def test_none_unless_exactly_one(raw):
    assert known.normal(raw) is None


def test_one_too_long_for_an_address_is_none():
    assert known.normal("x" * 250 + "@example.test") is None
    assert known.normal("x" * 240 + "@example.test") == "x" * 240 + "@example.test"


def test_the_input_is_cut_before_it_is_read():
    assert known.normal("lin@example.test" + " " * 400 + "and more") == "lin@example.test"


def test_an_address_is_looked_for_in_each_form_its_domain_takes():
    assert known.forms("lin@example.test") == {"lin@example.test"}
    assert known.forms("lin@bücher.example") == {"lin@bücher.example", "lin@xn--bcher-kva.example"}
    assert known.forms("lin@xn--bcher-kva.example") == {"lin@xn--bcher-kva.example", "lin@bücher.example"}


# -- what is asked ---------------------------------------------------------------

@pytest.mark.parametrize("addrs, says", [
    ("lin@example.test", "addrs must be a list"),
    ({"lin@example.test": 1}, "addrs must be a list"),
    (None, "addrs must be a list"),
    ([f"p{i}@example.test" for i in range(101)], "too many addresses"),
])
def test_a_request_that_isnt_a_list_a_message_could_hold_is_refused(addrs, says):
    with pytest.raises(ValueError, match=says):
        known.asked(addrs)


def test_as_many_as_a_message_can_hold_is_asked():
    assert len(known.asked([f"p{i}@example.test" for i in range(100)]).keys) == 100


def test_each_is_keyed_as_it_was_asked_and_looked_for_by_what_it_would_go_to():
    question = known.asked(["  Lin Ma <LIN@example.test> "])
    assert question.keys == ["lin ma <lin@example.test>"]
    assert question.want == {"lin@example.test": {"lin ma <lin@example.test>"}}
    assert question.bad == []


def test_two_ways_of_writing_one_address_answer_together():
    question = known.asked(["lin@example.test", "Lin <lin@example.test>"])
    assert question.want == {"lin@example.test": {"lin@example.test", "lin <lin@example.test>"}}


def test_what_no_mail_could_go_to_comes_back_as_bad_cut_short():
    question = known.asked(["Nobody", "x" * 300])
    assert question.bad == ["nobody", "x" * 200]
    assert (question.keys, question.want) == ([], {})


# -- your own ---------------------------------------------------------------------

def test_your_addresses_are_the_accounts_and_those_holding_mail_in_lowercase():
    assert known.own_addresses(["Ada@example.test"], ["Old@example.test"], ["HELD@example.test"]) == {
        "ada@example.test", "old@example.test", "held@example.test"}


# -- the answer -------------------------------------------------------------------

def test_your_own_is_known_and_says_so():
    question = known.asked(["ada@example.test"])
    assert known.verdicts(question, {"ada@example.test"}, set()) == {"ada@example.test": {"known": True, "self": True}}


def test_a_hit_is_known_and_no_one_else_is():
    question = known.asked(["lin@example.test", "bea@example.test"])
    assert known.verdicts(question, set(), {"lin@example.test"}) == {
        "lin@example.test": {"known": True, "self": False}, "bea@example.test": {"known": False, "self": False}}


def test_yours_beats_a_hit():
    question = known.asked(["ada@example.test"])
    assert known.verdicts(question, {"ada@example.test"}, {"ada@example.test"})["ada@example.test"]["self"] is True


def test_any_form_answers_for_the_address():
    question = known.asked(["lin@bücher.example"])
    assert known.verdicts(question, set(), {"lin@xn--bcher-kva.example"})["lin@bücher.example"]["known"] is True
    assert known.verdicts(question, set(), {"lin@bücher.example"})["lin@bücher.example"]["known"] is True


# -- what vouches -----------------------------------------------------------------

def facts(**kw):
    return known.Facts(**{"accounts": [], "senders": [], "synced": [], "draft_folders": set(), "copies": [], "envelopes": [], "thin": False, **kw})


def role(folder):
    return {"Spam": "junk", "Sent": "sent"}.get(folder, "custom")


OWN = {"ada@example.test"}
LIN = {"lin@example.test"}


def copy(**kw):
    return {"addr": "lin@example.test", "account": "ada@example.test", "folder": "INBOX", "is_junk": 0, "sender": "ada@example.test", **kw}


def vouched(**kw):
    return known.vouched(facts(**kw), LIN, OWN, role)


def test_mail_from_them_vouches_unless_it_sits_in_junk():
    assert vouched(senders=[("lin@example.test", "INBOX")]) == LIN
    assert vouched(senders=[("lin@example.test", "Spam")]) == set()


def test_mail_you_sent_them_vouches():
    assert vouched(copies=[copy()]) == LIN


@pytest.mark.parametrize("kw", [
    {"sender": "lin@example.test"},             # to them, but not from you
    {"is_junk": 1},                             # spam forging your From
    {"folder": "Spam"},
    {"folder": "My draft ideas"},
])
def test_mail_that_isnt_really_yours_doesnt(kw):
    assert vouched(copies=[copy(**kw)]) == set()


def test_the_autosaved_copy_of_what_is_being_written_never_vouches_for_its_own_recipient():
    assert vouched(copies=[copy(folder="Brouillons")], draft_folders={"Brouillons"}) == set()


def test_a_sent_folder_vouches_for_a_send_as_identity_that_isnt_an_account():
    sent_as = copy(folder="Sent", sender="alias@example.test")
    assert vouched(copies=[sent_as], synced=[("ada@example.test", "Sent")]) == LIN
    assert vouched(copies=[sent_as], synced=[("bea@example.test", "Sent")]) == set()


def test_no_other_synced_folder_does():
    filed = copy(folder="Archive", sender="alias@example.test")
    assert vouched(copies=[filed], synced=[("ada@example.test", "Archive")]) == set()


def test_a_delivered_envelope_vouches_for_those_asked_after_only():
    assert vouched(envelopes=[json.dumps(["LIN@example.test", "bea@example.test"])]) == LIN


@pytest.mark.parametrize("env", ["not json", "5", None])
def test_an_envelope_that_doesnt_read_says_nothing(env):
    assert vouched(envelopes=[env]) == set()
