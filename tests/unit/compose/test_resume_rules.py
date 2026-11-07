"""A reopened draft with no database and no server: which copy may be believed, and what comes back to the composer."""
import hashlib
import json

import pytest

from mailmind.compose.domain import limits, resume
from mailmind.compose.domain.errors import Refused

RAW = b"From: ada@example.test\r\nSubject: Plans\r\n\r\nHello\r\n"
SHA = hashlib.sha256(RAW).hexdigest()


def tracked(**kw):
    return {"uid": 9, "folder": "Drafts", "uidvalidity": 7, "raw_sha": None, **kw}


def opened(**kw):
    return {"uid": 9, "folder": "Drafts", **kw}


# -- what may be believed ------------------------------------------------------

def test_the_copy_the_row_tracks_is_the_same_copy():
    assert resume.trust(tracked(), opened(), 7, RAW) == resume.Trust(same_copy=True, exact=True)


@pytest.mark.parametrize("d, row, epoch", [
    (tracked(uid=None), opened(), 7),                    # a row with no copy yet tracks none
    (tracked(uid=None), opened(uid=None), 7),            # however little the other says
    (tracked(uid=8), opened(), 7),
    (tracked(), opened(uid=8), 7),
    (tracked(), opened(folder="Brouillons"), 7),
    (tracked(), opened(), 8),                            # renumbered: the same UID is another message
])
def test_any_other_copy_is_not(d, row, epoch):
    assert resume.trust(d, row, epoch, RAW) == resume.Trust(same_copy=False, exact=False)


@pytest.mark.parametrize("d, epoch", [(tracked(uidvalidity=None), 7), (tracked(), None)])
def test_an_epoch_not_known_on_either_side_doesnt_count_against_it(d, epoch):
    assert resume.trust(d, opened(), epoch, RAW).same_copy is True


def test_the_same_bytes_vouch_for_another_copy_but_dont_make_it_the_same_one():
    assert resume.trust(tracked(raw_sha=SHA, uid=8), opened(), 7, RAW) == resume.Trust(same_copy=False, exact=True)


def test_other_bytes_dont():
    assert resume.trust(tracked(raw_sha="0" * 64, uid=8), opened(), 7, RAW).exact is False
    assert resume.sha_of(RAW) == SHA


# -- whether the copy has gone ------------------------------------------------

def test_a_row_that_still_says_the_same_hasnt_moved():
    row = {"uid": 9, "message_id": "<a@example.test>"}
    assert resume.copy_moved(row, dict(row)) is False


@pytest.mark.parametrize("now", [None, {"uid": 10, "message_id": "<a@example.test>"}, {"uid": 9, "message_id": "<b@example.test>"}])
def test_a_save_or_a_send_meanwhile_moved_it(now):
    assert resume.copy_moved({"uid": 9, "message_id": "<a@example.test>"}, now) is True


def test_with_no_row_before_there_is_nothing_to_have_moved():
    assert resume.copy_moved(None, None) is False


@pytest.mark.parametrize("seen, known, there, says", [
    (7, 7, True, True), (7, 7, False, False),
    (8, 7, True, False),                                 # renumbered: every UID held is void
    (None, 7, True, True), (7, None, True, True),        # nothing to compare
])
def test_what_a_probe_says_goes_unless_the_folder_was_renumbered(seen, known, there, says):
    assert resume.copy_is_there(seen, known, there) is says


# -- what comes back ----------------------------------------------------------

def record(**kw):
    return {"own_text": None, "tail_text": None, **kw}


def test_formatted_text_alone_cant_come_back_without_losing_it():
    with pytest.raises(Refused) as refused:
        resume.split_to_fit(None, record(), False, [])
    assert refused.value.code == 422 and "formatted text" in refused.value.msg


def test_what_the_record_says_holds_for_an_exact_copy_and_not_otherwise():
    kept = record(own_text="Mine", tail_text="\n\nquoted")
    assert resume.split_to_fit("server's text", kept, True, []) == ("Mine", "\n\nquoted", "exact")
    assert resume.split_to_fit("server's text", kept, False, []) == ("server's text", "", "whole")


def test_a_draft_just_too_large_to_save_is_refused_and_one_just_small_enough_isnt():
    room = limits.MAX_SEND_POST - limits.RESUME_HEADROOM
    empty_text = len(json.dumps("")) * 2
    fits = [{"filename": "a", "content_type": "text/plain", "size": 0, "b64": "x" * (room - empty_text)}]
    assert resume.split_to_fit("", record(own_text=""), True, fits) == ("", "", "exact")
    over = [{"filename": "a", "content_type": "text/plain", "size": 0, "b64": "x" * (room - empty_text + 1)}]
    with pytest.raises(Refused) as refused:
        resume.split_to_fit("", record(own_text=""), True, over)
    assert refused.value.code == 422 and refused.value.msg.endswith(resume.ELSEWHERE)


def test_the_size_counts_the_text_as_the_bytes_of_its_json():
    room = limits.MAX_SEND_POST - limits.RESUME_HEADROOM
    with pytest.raises(Refused):
        resume.split_to_fit("", record(own_text="é" * (room // 2)), True, [])     # two bytes each, not one


@pytest.mark.parametrize("stored, irt, mode", [
    ("reply", "<o@example.test>", "reply"), ("replyall", "<o@example.test>", "replyall"), ("forward", "<o@example.test>", "forward"),
    ("new", None, "new"), (None, None, "new"), ("nonsense", None, "new"),
    ("new", "<o@example.test>", "reply"),                # MailMind's own new drafts never carry one
    ("nonsense", "<o@example.test>", "reply"),
    ("reply", None, "reply"),
])
def test_a_draft_reopens_in_the_mode_it_was_saved_in_as_far_as_its_headers_allow(stored, irt, mode):
    assert resume.mode_of(stored, irt) == mode


def test_those_a_reply_all_could_reach_are_each_named_once_in_lowercase():
    original = {"from_addr": "Lin@Example.test", "recipients": 'Ada <ada@example.test>, "Bea" <BEA@example.test>; lin@example.test, nobody'}
    assert resume.participants_of(original) == ["lin@example.test", "ada@example.test", "bea@example.test"]


def test_a_row_naming_no_one_gives_no_one():
    assert resume.participants_of({"from_addr": None, "recipients": None}) == []


def message(bcc=None, subject="Plans"):
    head = f"Subject: {subject}\r\n" if subject else ""
    if bcc:
        head += f"Bcc: {bcc}\r\n"
    return resume.read(f"From: ada@example.test\r\n{head}\r\nHello\r\n".encode())[0]


def test_the_records_bcc_joins_an_exact_copy():
    assert resume.bcc_of(message(), json.dumps(["Lin <lin@example.test>"]), True) == (["Lin <lin@example.test>"], 0)


def test_on_any_other_copy_it_is_only_counted():
    assert resume.bcc_of(message(), json.dumps(["Lin <lin@example.test>"]), False) == ([], 1)


def test_one_the_draft_already_names_isnt_named_twice():
    assert resume.bcc_of(message("lin@example.test"), json.dumps(["LIN@example.test", "bea@example.test"]), True) == (
        ["lin@example.test", "bea@example.test"], 0)


def test_and_isnt_counted_as_dropped():
    assert resume.bcc_of(message("lin@example.test"), json.dumps(["LIN@example.test"]), False) == (["lin@example.test"], 0)


def test_a_recorded_chip_naming_no_address_counts_for_nothing():
    assert resume.bcc_of(message(), json.dumps(["not an address"]), False) == ([], 0)
    assert resume.bcc_of(message(), json.dumps(["not an address"]), True) == ([], 0)


def test_no_record_is_no_bcc():
    assert resume.bcc_of(message(), None, True) == ([], 0)


def test_the_subject_is_as_drafted_and_else_what_the_row_said():
    assert resume.subject_of(message(subject="Plans"), "Row's") == "Plans"
    assert resume.subject_of(message(subject=None), "Row's") == "Row's"
    assert resume.subject_of(message(subject=None), None) == ""
