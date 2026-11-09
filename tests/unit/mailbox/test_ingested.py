"""What a message's headers come to as it is stored (mailmind.mailbox.domain.ingested): values in and values out."""
from mailmind.mailbox.domain import ingested


def test_to_and_cc_are_one_searchable_string():
    assert ingested.recipients_text("Ada <ada@example.test>", "bo@example.test") == "Ada <ada@example.test> bo@example.test"


def test_a_missing_side_leaves_no_stray_space():
    assert ingested.recipients_text("ada@example.test", None) == "ada@example.test"
    assert ingested.recipients_text(None, "bo@example.test") == "bo@example.test"
    assert ingested.recipients_text(None, None) == ""


def test_the_text_kept_is_bounded():
    assert len(ingested.recipients_text("x" * (ingested.RECIPIENTS_CHARS + 5), None)) == ingested.RECIPIENTS_CHARS


def test_a_date_reads_as_iso_and_unix_time():
    assert ingested.dated("Mon, 02 Nov 2026 12:00:00 +0100") == ("2026-11-02T12:00:00+01:00", 1793617200)


def test_a_date_with_no_zone_reads_as_utc():
    assert ingested.dated("Mon, 02 Nov 2026 12:00:00 -0000") == ("2026-11-02T12:00:00+00:00", 1793620800)


def test_a_date_that_wont_read_is_none_and_nought():
    assert ingested.dated("not a date") == (None, 0)
    assert ingested.dated("") == (None, 0)


def test_a_folded_references_header_reads_as_one_line():
    assert ingested.references("<a@example.test>\n <b@example.test>\n") == "<a@example.test>  <b@example.test>"


def test_no_references_is_empty_text():
    assert ingested.references(None) == ""
