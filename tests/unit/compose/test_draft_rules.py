"""A draft's rules with no database and no server: what makes a save, whether one may go in, what is a draft and which copy
is ours to remove."""
import base64

import pytest

from mailmind.compose.domain import draft


def spec(**kw):
    return {"account": "ada@example.test", "account_name": "Ada", "to": [], "cc": [], "bcc": [], "subject": "", "text": "",
            "html": None, "attachments": [], "in_reply_to": None, "references": None, "own": "",
            "tail": {"text": None, "html": None}, "forwarded": [], **kw}


def asked_for(*_):
    raise AssertionError("the rule had its answer and asked anyway")


# -- the composer's key ---------------------------------------------------------

@pytest.mark.parametrize("given, key", [
    ({"idempotency_key": "k-1"}, "k-1"), ({"idempotency_key": "  k-1 "}, "k-1"), ({"idempotency_key": 12}, "12"),
    ({"idempotency_key": "x" * 300}, "x" * 200),
    ({}, None), ({"idempotency_key": ""}, None), ({"idempotency_key": "   "}, None), ({"idempotency_key": None}, None),
])
def test_a_composers_key_is_what_it_gave_cut_and_trimmed_or_none(given, key):
    assert draft.composer_key(given) == key


# -- what makes a save ----------------------------------------------------------

@pytest.mark.parametrize("empty", [{}, {"subject": "   "}, {"text": " \n "}, {"text": None}])
def test_nothing_typed_is_empty(empty):
    assert draft.is_empty(spec(**empty)) is True


@pytest.mark.parametrize("kept", [
    {"subject": "Plans"}, {"text": "Hello"}, {"to": ["lin@example.test"]}, {"cc": ["lin@example.test"]}, {"bcc": ["lin@example.test"]},
    {"attachments": [{"filename": "a.txt", "content_type": "text/plain", "data": b""}]},
])
def test_any_one_of_them_is_worth_saving(kept):
    assert draft.is_empty(spec(**kept)) is False


# -- whether a save may go in ---------------------------------------------------

def test_a_save_that_saw_the_latest_goes_in():
    assert draft.is_stale({"version": 3, "saved_by": "other"}, 3, "mine") is False


def test_one_that_never_did_doesnt():
    assert draft.is_stale({"version": 4, "saved_by": "other"}, 3, "mine") is True


def test_unless_the_last_save_was_this_composers_whose_answer_was_lost():
    assert draft.is_stale({"version": 4, "saved_by": "mine"}, 3, "mine") is False


def test_a_composer_with_no_key_can_claim_no_save_as_its_own():
    assert draft.is_stale({"version": 4, "saved_by": None}, 3, None) is True


def test_a_save_naming_no_version_overwrites_since_someone_asked_for_that():
    assert draft.is_stale({"version": 4, "saved_by": "other"}, None, "mine") is False


def test_versions_count_from_one():
    assert draft.next_version(None) == 1
    assert draft.next_version({"version": 4}) == 5


# -- what a draft is ------------------------------------------------------------

def test_a_message_the_server_flags_is_a_draft_wherever_it_is():
    assert draft.is_draft({"draft": 1, "account": "ada@example.test", "folder": "Contract drafts"}, asked_for) is True


def test_one_in_the_accounts_drafts_is_a_draft_without_the_flag():
    plays = {("ada@example.test", "Entwürfe")}
    assert draft.is_draft({"draft": 0, "account": "ada@example.test", "folder": "Entwürfe"}, lambda a, f: (a, f) in plays) is True
    assert draft.is_draft({"draft": 0, "account": "ada@example.test", "folder": "Contract drafts"}, lambda a, f: (a, f) in plays) is False


# -- whose copy it is -----------------------------------------------------------

def copy(**kw):
    return {"uid": 9, "raw_sha": None, "folder": "Contract drafts", **kw}


def test_a_copy_still_only_in_the_journal_has_nothing_to_remove():
    assert draft.copy_is_ours(copy(uid=None, raw_sha="abc"), asked_for, asked_for) is False


def test_one_mailmind_saved_is_ours_wherever_it_is_with_no_server_asked():
    assert draft.copy_is_ours(copy(raw_sha="abc"), asked_for, asked_for) is True


def test_one_in_a_folder_named_drafts_is_ours():
    assert draft.copy_is_ours(copy(folder="Drafts"), lambda f: f == "Drafts", asked_for) is True


def test_received_mail_in_a_folder_merely_holding_the_word_stays_unless_flagged():
    assert draft.copy_is_ours(copy(), lambda f: False, lambda: False) is False
    assert draft.copy_is_ours(copy(), lambda f: False, lambda: True) is True


# -- what a push reaches --------------------------------------------------------

def prev(**kw):
    return {"account": "ada@example.test", "folder": "Drafts", "uid": 9, "uidvalidity": 7, "message_id": "<old@example.test>", **kw}


SAME = {"account": "ada@example.test"}
OTHER = {"account": "bea@example.test"}


@pytest.mark.parametrize("row, waiting, spans", [
    (prev(), SAME, False),
    (prev(), OTHER, True),                  # the old copy is there to remove
    (prev(uid=None), OTHER, False),         # with no copy, only the new account is asked
])
def test_a_push_spans_accounts_only_for_an_old_copy_in_another(row, waiting, spans):
    assert draft.spans_accounts(row, waiting) is spans


def test_a_push_replaces_in_place_what_is_in_its_own_mailbox():
    assert draft.replaced_in_place(prev(), SAME) == {"prev_uid": 9, "prev_uidvalidity": 7, "prev_msgid": "<old@example.test>",
                                                     "prev_folder": "Drafts"}


def test_another_mailboxs_uid_names_nothing_here():
    assert draft.replaced_in_place(prev(), OTHER) == {"prev_uid": None, "prev_uidvalidity": None, "prev_msgid": None,
                                                      "prev_folder": None}


@pytest.mark.parametrize("old, j, folder, left", [
    (prev(), SAME, "Drafts", False),                    # the APPEND replaced it
    (prev(), SAME, "Brouillons", True),                 # a draft reopened from another folder
    (prev(), OTHER, "Drafts", True),                    # From changed
    (prev(folder=None), SAME, "Drafts", False),
    (prev(uid=None), OTHER, "Drafts", False),           # no copy to speak of
    (prev(uidvalidity=None), OTHER, "Drafts", False),   # a UID nothing vouches for stays
])
def test_a_copy_left_elsewhere_is_one_the_append_couldnt_reach(old, j, folder, left):
    assert draft.left_elsewhere(old, j, folder) is left


# -- what the composer is told --------------------------------------------------

def test_a_first_save_hands_the_composer_the_quote_the_threading_and_the_files():
    composed = spec(in_reply_to="<o@example.test>", references=["<r@example.test>", "<o@example.test>"],
                    tail={"text": "\r\n\r\n> quoted\r\n", "html": None},
                    forwarded=[{"filename": "a.txt", "content_type": "text/plain", "data": b"hi"}])
    assert draft.handed_over(composed) == {
        "resume": {"in_reply_to": "<o@example.test>", "references": "<r@example.test> <o@example.test>", "tail_text": "\n\n> quoted\n"},
        "files": [{"filename": "a.txt", "content_type": "text/plain", "size": 2, "b64": base64.b64encode(b"hi").decode()}]}


def test_a_message_with_no_original_hands_over_empty_threading():
    assert draft.handed_over(spec(tail={"text": "x", "html": None}))["resume"] == {"in_reply_to": "", "references": "", "tail_text": "x"}


# -- a new row ------------------------------------------------------------------

def test_a_new_rows_copy_is_nowhere_yet_and_both_stamps_are_now():
    assert draft.tracked("ada@example.test", "<n@example.test>", "Plans", "reply", "then", in_reply_to_pk=4) == {
        "id": None, "account": "ada@example.test", "folder": None, "uid": None, "message_id": "<n@example.test>", "uidvalidity": None,
        "in_reply_to_pk": 4, "mode": "reply", "subject": "Plans", "created_at": "then", "updated_at": "then"}


def test_another_apps_draft_says_where_its_copy_is():
    row = draft.tracked("ada@example.test", "<n@example.test>", "Plans", "new", "then", folder="Drafts", uid=9, uidvalidity=7)
    assert (row["folder"], row["uid"], row["uidvalidity"], row["in_reply_to_pk"]) == ("Drafts", 9, 7, None)
