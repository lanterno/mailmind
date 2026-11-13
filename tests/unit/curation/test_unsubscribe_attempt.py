"""What trying to leave a list comes to, with no database, no server and no I/O. That a repeat never fires again, on the wire,
is tests/api/test_http_cleanup.py."""
import pytest

from mailmind.curation.domain import unsubscribe
from mailmind.curation.domain.unsubscribe import (NO_METHOD, READ_ONLY, TERMINAL, Attempt, Mailto, after_mailto, after_one_click,
                                                  attempt_key, mailto_of, not_tried, offers_a_way, preview, preview_said, repeat_of,
                                                  said, without_sending)

PAGE = {**NO_METHOD, "method": "http-page", "https_uri": "https://list.example.test/leave?t=1"}
ONE_CLICK = {**NO_METHOD, "method": "one-click", "https_uri": "https://list.example.test/u?t=1"}
MAILTO = {**NO_METHOD, "method": "mailto", "mailto_to": "leave@list.example.test"}


# ── what is latched ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("attempt, latched", [
    (after_one_click(True, 200), True),
    (after_one_click(False, 500), False),
    (after_mailto(True), True),
    (after_mailto(False), False),
    (without_sending(PAGE), True),
    (without_sending(NO_METHOD), False),
    (READ_ONLY, False),
    (not_tried(True), False),
    (not_tried(False), False),
])
def test_only_what_got_somewhere_is_latched(attempt, latched):
    assert attempt.latched is latched
    assert (attempt.status in TERMINAL) is latched


def test_an_attempt_is_keyed_by_its_sender():
    assert attempt_key("ada@example.test") == "unsub:ada@example.test"


# ── what each way comes to ────────────────────────────────────────────────────
def test_a_one_click_reports_the_servers_answer_either_way():
    assert after_one_click(True, 200) == Attempt("unsubscribed_oneclick", "one-click", 200)
    assert after_one_click(False, 503) == Attempt("failed", "one-click", 503)
    assert after_one_click(False, None) == Attempt("failed", "one-click", None)


def test_a_mailto_says_whether_any_went():
    assert after_mailto(True) == Attempt("mailto_sent", "mailto")
    assert after_mailto(False) == Attempt("failed", "mailto")


def test_a_page_is_for_the_person_to_open():
    assert without_sending(PAGE) == Attempt("needs_browser", "http-page", None, PAGE["https_uri"])


def test_no_way_out_says_so():
    assert without_sending(NO_METHOD) == Attempt("no_method", "none")


def test_read_only_sends_nothing_and_stays_retryable():
    assert READ_ONLY == Attempt("read_only", "none")


def test_one_that_did_not_go_says_why():
    assert not_tried(True) == Attempt("no_token", "none")           # asked, with no good token: nothing goes outside
    assert not_tried(False) == Attempt("skipped", "none")           # nobody asked


# ── a repeat ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("prior", [None, {"status": "failed", "method": "one-click"}, {"status": "no_method", "method": "none"}])
def test_what_isnt_latched_is_tried_again(prior):
    assert repeat_of(prior, ONE_CLICK) is None


@pytest.mark.parametrize("status, method", [("unsubscribed_oneclick", "one-click"), ("mailto_sent", "mailto")])
def test_a_repeat_answers_what_it_came_to_with_nothing_to_open(status, method):
    assert repeat_of({"status": status, "method": method, "http_status": 200}, PAGE) == Attempt(status, method)


def test_a_repeat_of_a_page_can_be_opened_again():
    assert repeat_of({"status": "needs_browser", "method": "http-page"}, PAGE) == Attempt("needs_browser", "http-page", None, PAGE["https_uri"])


# ── what a preview shows ──────────────────────────────────────────────────────
@pytest.mark.parametrize("info, shown", [
    (ONE_CLICK, {"method": "one-click", "https_host": "list.example.test"}),
    (PAGE, {"method": "http-page", "https_host": "list.example.test"}),
    ({**PAGE, "https_uri": None}, {"method": "http-page", "https_host": None}),
    (MAILTO, {"method": "mailto", "mailto_to": "leave@list.example.test"}),
    (NO_METHOD, {"method": "none"}),
])
def test_a_preview_shows_the_way_and_only_where_it_goes(info, shown):
    assert preview(info) == shown


def test_what_a_token_stands_for_is_what_would_go_out_and_where():
    assert preview_said("Ada@Example.test", ONE_CLICK) == ("ada@example.test", "one-click", ONE_CLICK["https_uri"])
    assert preview_said("ada@example.test", MAILTO) == ("ada@example.test", "mailto", "leave@list.example.test")


# ── no way out ────────────────────────────────────────────────────────────────
def test_no_way_out_reads_one_way_however_a_message_said_it():
    assert not offers_a_way({**NO_METHOD, "raw": "something unusable"})
    assert said({**NO_METHOD, "raw": "something unusable"}) == NO_METHOD
    assert said(ONE_CLICK) == ONE_CLICK and offers_a_way(ONE_CLICK)


# ── a mailto ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("to", ["", None, "no-at-sign", "two@at@example.test", "a b@example.test", "a@example.test,b@example.test",
                                "a@example.test\r\nBcc: b@example.test", "<a@example.test>", 'a"@example.test', "a@", "@example.test",
                                "a:b@example.test", "a;b@example.test", "(a)@example.test", "[a]@example.test", "a\\b@example.test"])
def test_a_mailto_goes_to_one_plain_address_or_not_at_all(to):
    assert mailto_of({**MAILTO, "mailto_to": to}) is None


def test_a_list_says_nothing_and_the_mailto_asks_plainly():
    assert mailto_of(MAILTO) == Mailto("leave@list.example.test", "unsubscribe", "Please unsubscribe me.")


def test_a_subject_stays_on_one_line_and_within_its_length():
    assert mailto_of({**MAILTO, "mailto_subject": "stop\r\nBcc: a@example.test"}).subject == "stop  Bcc: a@example.test"
    assert len(mailto_of({**MAILTO, "mailto_subject": "x" * 500}).subject) == unsubscribe.SUBJECT_MAX == 200


def test_a_text_keeps_a_paragraph_break_and_no_more_and_stays_within_its_length():
    assert mailto_of({**MAILTO, "mailto_body": "a\r\n\r\n\r\nb\n\nc\nd"}).text == "a\n\nb\n\nc\nd"
    assert len(mailto_of({**MAILTO, "mailto_body": "x" * 5000}).text) == unsubscribe.TEXT_MAX == 1000
