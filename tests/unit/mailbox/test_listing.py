"""What a row of a list says (mailmind.mailbox.domain.listing): values in and values out, with how a time is said handed in.
The schema contract and the golden lists hold the answers these make; these hold each rule alone."""
import pytest

from mailmind.mailbox.domain import listing


def when(iso):
    return f"when:{iso}"


def ago(ts):
    return f"{ts}h"


def wakes(ts):
    return f"at:{ts}"


def row(**over):
    return {"pk": 1, "message_key": 7, "folder": "INBOX", "uid": 3, "from_name": "Ada", "from_addr": "ada@example.test", "subject": "Lunch",
            "date_iso": "2026-11-02", "snippet": "See you", "seen": 1, "flagged": 0, "has_attach": 0, "is_junk": 0, "bucket": "personal",
            "importance": 4, "category": "personal", "action": "reply", "thread_id": "rf:1", "summary": "Lunch plans", "ts": 100,
            "account": "ada@example.test", "message_id": "<a@example.test>", "answered": 0, **over}


def test_a_row_names_its_copy():
    it = listing.listed_item(row(), False, when)
    assert (it["key"], it["folder"], it["uid"]) == (7, "INBOX", 3)


def test_a_sender_with_no_name_shows_the_address():
    assert listing.listed_item(row(from_name=""), False, when)["from_name"] == "ada@example.test"


def test_a_message_with_no_subject_says_so():
    assert listing.listed_item(row(subject=""), False, when)["subject"] == "(no subject)"


def test_a_long_snippet_and_summary_are_cut():
    it = listing.listed_item(row(snippet="x" * 500, summary="y" * 500), False, when)
    assert len(it["snippet"]) == 200 and len(it["summary"]) == 300


def test_nothing_where_a_snippet_or_summary_would_be_is_empty_text():
    it = listing.listed_item(row(snippet=None, summary=None, action=None), False, when)
    assert (it["snippet"], it["summary"], it["action"]) == ("", "", "")


def test_a_row_says_when_as_it_was_told_to():
    assert listing.listed_item(row(), False, when)["when"] == "when:2026-11-02"


def test_a_row_carries_what_the_verdict_asks_of_you():
    assert "needs_you" in listing.listed_item(row(), False, when)


def conversation(**over):
    return row(cnt=3, thread_count=5, unread=1, any_flagged=1, any_attach=1, **over)


def test_a_conversations_row_rolls_up_its_messages():
    it = listing.listed_item(conversation(), True, when)
    assert (it["count"], it["thread_count"], it["seen"], it["flagged"], it["has_attach"]) == (3, 5, False, True, True)


def test_a_conversation_with_no_thread_count_says_how_many_the_list_holds():
    assert listing.listed_item(row(cnt=3, thread_count=None, unread=0, any_flagged=0, any_attach=0), True, when)["thread_count"] == 3


def test_a_plain_row_has_no_rollup():
    assert "count" not in listing.listed_item(row(), False, when)


def test_a_snoozed_row_says_when_it_wakes():
    it = listing.snoozed_item(row(snoozed_until=900), wakes)
    assert it["when"] == "wakes at:900" and it["snoozed_until"] == 900


def test_a_muted_row_counts_as_read_whatever_its_flag_says():
    it = listing.muted_item(row(seen=0, thread_count=4), when)
    assert it["seen"] is True and it["muted"] is True and it["count"] == 4


def waiting(**over):
    return row(f_kind="received", thread_count=2, recipients="bo@example.test, cy@example.test", f_first_sent=8, **over)


def test_a_thread_waiting_on_you_says_since_when():
    it = listing.waiting_item(waiting(), when, ago)
    assert it["waiting_label"] == "Awaiting your reply · when:2026-11-02" and it["kind"] == "received"


def test_a_thread_you_sent_says_how_long_it_has_waited_and_on_whom():
    it = listing.waiting_item({**waiting(), "f_kind": "sent"}, when, ago)
    assert it["waiting_label"] == "Sent 8h ago · no reply" and it["to"] == "bo@example.test"


def test_a_thread_with_no_recipients_waits_on_no_one():
    assert listing.waiting_item({**waiting(), "recipients": None}, when, ago)["to"] == ""


def test_reply_later_says_so():
    it = listing.replylater_item(row(thread_count=2), when)
    assert it["replylater"] is True and it["waiting_label"] == "Reply later"


def test_a_message_held_by_two_folders_is_one_message_in_its_conversation():
    items = listing.thread_items([row(pk=1, folder="INBOX"), row(pk=2, folder="[Gmail]/All Mail")], when)
    assert [i["folder"] for i in items] == ["INBOX"]


def test_messages_with_no_message_id_are_never_the_same_as_each_other():
    assert len(listing.thread_items([row(pk=1, message_id=""), row(pk=2, message_id=None)], when)) == 2


def test_a_conversations_message_says_whether_it_was_answered():
    assert listing.thread_items([row(answered=1)], when)[0]["answered"] is True


@pytest.mark.parametrize("shown, total, offset, more", [(2, 5, 0, True), (2, 5, 3, False), (0, 0, 0, False), (5, 5, 0, False)])
def test_a_page_says_whether_there_is_more(shown, total, offset, more):
    assert listing.window([None] * shown, total, offset) == {"total": total, "offset": offset, "has_more": more}
