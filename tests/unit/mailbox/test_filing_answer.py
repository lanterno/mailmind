"""What a person hears of filing, and how a request's marked rows read (mailmind.mailbox.domain.filing_answer and .filing).
Values in and values out: test_file_many.py and test_thread_filing.py run them through the routes."""
import pytest

from mailmind.kernel.errors import Conflict, NotFound, Refused
from mailmind.mailbox.domain import command, filing, filing_answer


# -- the words --------------------------------------------------------------

@pytest.mark.parametrize("kind, outcome, words", [
    ("archive", command.NO_FOLDER, "no archive folder on this account"),
    ("junk", command.NO_FOLDER, "no junk folder on this account"),
    ("archive", command.REFUSED, "the server refused the archive"),
    ("trash", command.REFUSED, "the server refused the move"),
    ("trash", command.NO_FOLDER, "the server refused the move"),
])
def test_what_a_refusal_says(kind, outcome, words):
    assert filing_answer.refusal(kind, outcome) == words


def test_every_way_of_filing_has_its_word():
    assert set(filing_answer.FILED) == {"trash", "archive", "junk", "move"}


# -- many moves as one answer -------------------------------------------------

NAMES = {1: (10, "INBOX", 100), 2: (20, "INBOX", 200)}
FILED = {"ok": True, "undoable": True}
QUEUED = {"ok": True, "status": "queued", "undoable": False}


def accepted(*pks):
    return [(p, None) for p in pks]


def test_all_filed_is_named_and_counted():
    said = filing_answer.of_many(accepted(1, 2), {1: FILED, 2: FILED}, NAMES)
    assert said == {"ok": True, "msg": "filed", "count": 2, "failed": 0, "queued": 0, "undoable": True,
                    "filed": [{"key": 10, "folder": "INBOX", "uid": 100}, {"key": 20, "folder": "INBOX", "uid": 200}]}


def test_one_refused_goes_back_alone_and_the_rest_stay_filed():
    said = filing_answer.of_many(accepted(1, 2), {1: FILED, 2: Conflict("no")}, NAMES)
    assert said["count"] == 1 and said["failed"] == 1 and [f["key"] for f in said["filed"]] == [10]


def test_any_queued_says_so():
    said = filing_answer.of_many(accepted(1, 2), {1: FILED, 2: QUEUED}, NAMES)
    assert said["msg"] == "queued" and said["queued"] == 1


def test_undoable_if_any_was():
    assert not filing_answer.of_many(accepted(1), {1: QUEUED}, NAMES)["undoable"]


def test_a_message_whose_name_has_gone_is_counted_and_not_named():
    said = filing_answer.of_many(accepted(3), {3: FILED}, NAMES)
    assert said["count"] == 1 and said["filed"] == []


def test_with_none_filed_it_raises_what_the_first_did():
    with pytest.raises(Conflict, match="first"):
        filing_answer.of_many(accepted(1, 2), {1: Conflict("first"), 2: NotFound("second")}, NAMES)


def test_with_nothing_to_file_it_is_not_found():
    with pytest.raises(NotFound, match="not found"):
        filing_answer.of_many([], {}, NAMES)


def test_a_row_failed_when_none_of_its_messages_went_and_is_partial_when_only_some_did():
    results = {1: FILED, 2: Conflict("no"), 3: Conflict("no")}
    assert filing_answer.rows_tally(results, [[1], [2, 3], [1, 2]]) == {"rows_failed": 1, "partial": True}


def test_rows_whose_messages_all_went_are_neither():
    assert filing_answer.rows_tally({1: FILED}, [[1]]) == {"rows_failed": 0, "partial": False}


def test_a_row_whose_copy_has_gone_stands_for_nothing_and_counts_as_failed():
    assert filing_answer.rows_tally({}, [[]]) == {"rows_failed": 1, "partial": False}


# -- what a request's marked rows name ---------------------------------------

def test_a_marked_row_names_its_copy_and_whether_it_is_the_conversation():
    assert filing.marked_rows([{"key": 7, "folder": "INBOX", "uid": 3, "whole": True}], 500, True) == [((7, "INBOX", 3), True)]


def test_a_row_with_no_uid_names_only_the_copy_there():
    assert filing.marked_rows([{"key": 7, "folder": "INBOX"}], 500, True) == [((7, "INBOX", None), False)]


def test_whole_counts_only_where_the_verb_allows_and_only_when_it_is_true():
    assert filing.marked_rows([{"key": 7, "folder": "INBOX", "whole": True}], 500, False)[0][1] is False
    assert filing.marked_rows([{"key": 7, "folder": "INBOX", "whole": 1}], 500, True)[0][1] is False


@pytest.mark.parametrize("rows", [None, [], "rows", ["not-an-object"], [{"folder": "INBOX"}], [{"key": 7}], [{"key": 7, "folder": 3}],
                                  [{"key": 7, "folder": "INBOX", "uid": "x"}]],
                         ids=["none", "empty", "text", "not-object", "no-key", "no-folder", "folder-not-text", "bad-uid"])
def test_rows_that_wont_read_are_refused_whole(rows):
    with pytest.raises(Refused):
        filing.marked_rows(rows, 500, True)


def test_one_row_over_the_limit_refuses_them_all():
    rows = [{"key": i, "folder": "INBOX"} for i in range(1, 4)]
    with pytest.raises(Refused):
        filing.marked_rows(rows, 2, True)
    assert len(filing.marked_rows(rows, 3, True)) == 3


def test_junk_is_never_of_a_whole_conversation():
    assert not filing.takes_conversations("junk")
    assert filing.takes_conversations("archive") and filing.takes_conversations("trash")


@pytest.mark.parametrize("thread_id, threaded", [("rf:1", True), ("", False), (None, False), ("  ", False)])
def test_a_message_with_no_thread_is_a_conversation_of_one(thread_id, threaded):
    assert filing.has_thread({"thread_id": thread_id}) is threaded
