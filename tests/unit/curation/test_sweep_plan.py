"""What a sweep would take, with no server, no database and no I/O. Moving it is tests/e2e/test_cli.py and test_http_cleanup."""
import pytest

from mailmind.curation.domain import classifier, rulebook, sweep_plan
from mailmind.curation.domain.classifier import BLOCKED, JUNK_BUCKETS, MKT, PERSONAL, PROTECTED
from mailmind.curation.domain.live import READ_FIELDS, read_from_headers

NO_RULES = rulebook.as_sets(rulebook.empty())


def rules(**lists):
    return {**NO_RULES, **{k: frozenset(v) for k, v in lists.items()}}


def read(sender, mid="<a@example.test>", subject="Hello", name="", bulk=False, uid="1"):
    return {"uid": uid, "from": sender, "name": name, "subject": subject, "mid": mid, "bulk": bulk}


def stored(sender, bucket=PERSONAL, subject="Hello", name="", list_unsub=""):
    return {"from_addr": sender, "from_name": name, "subject": subject, "bucket": bucket, "list_unsub_raw": list_unsub}


# ── how an inbox falls ────────────────────────────────────────────────────────
def test_an_inbox_falls_into_its_buckets():
    got = sweep_plan.tally([(MKT, "a@example.test"), (MKT, "b@example.test"), (PERSONAL, "c@example.test")])
    assert (got["in_inbox"], got["junk"], got["keep"]) == (3, 2, 1)
    assert got["by_bucket"] == {MKT: 2, PERSONAL: 1}


def test_only_junk_buckets_count_as_junk():
    for bucket in JUNK_BUCKETS:
        assert sweep_plan.tally([(bucket, "a@example.test")])["junk"] == 1
    for bucket in (PERSONAL, PROTECTED, classifier.RECEIPT):
        assert sweep_plan.tally([(bucket, "a@example.test")])["keep"] == 1


def test_top_senders_are_the_four_who_wrote_most_most_first():
    mail = [(MKT, f"s{n}@example.test") for n in (1, 2, 2, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 5)]
    assert sweep_plan.tally(mail)["top_senders"][MKT] == ["s5@example.test", "s4@example.test", "s3@example.test", "s2@example.test"]


def test_every_junk_bucket_is_named_among_top_senders_and_only_those_holding_mail_in_by_bucket():
    got = sweep_plan.tally([(PERSONAL, "a@example.test")])
    assert got["by_bucket"] == {PERSONAL: 1}
    assert got["top_senders"] == {PERSONAL: ["a@example.test"], **{b: [] for b in JUNK_BUCKETS}}


def test_an_empty_inbox_comes_to_nothing():
    got = sweep_plan.tally([])
    assert (got["in_inbox"], got["by_bucket"], got["junk"], got["keep"]) == (0, {}, 0, 0)


# ── mail read live ────────────────────────────────────────────────────────────
def test_a_message_is_read_from_its_headers():
    raw = (b"From: =?utf-8?b?QWRh?= <Ada@Example.TEST>\r\nSubject: =?utf-8?b?SGVsbG8=?=\r\nMessage-ID:  <m@example.test> \r\n\r\n")
    assert read_from_headers(7, raw) == {"uid": "7", "from": "ada@example.test", "name": "Ada", "subject": "Hello",
                                         "mid": "<m@example.test>", "bulk": False}


@pytest.mark.parametrize("header, bulk", [(b"List-Unsubscribe: <https://list.example.test/u>\r\n", True),
                                          (b"List-ID: <news.example.test>\r\n", True), (b"X-Other: 1\r\n", False)])
def test_either_list_header_says_it_is_bulk(header, bulk):
    assert read_from_headers(1, b"From: a@example.test\r\n" + header + b"\r\n")["bulk"] is bulk


def test_a_sweep_asks_the_server_for_what_it_reads():
    assert {"FROM", "SUBJECT", "MESSAGE-ID", "LIST-UNSUBSCRIBE", "LIST-ID"} == set(READ_FIELDS)


def test_a_message_with_no_id_reads_as_none():
    assert read_from_headers(1, b"From: a@example.test\r\n\r\n")["mid"] == ""


def test_what_is_read_gets_the_classifiers_word():
    [got] = sweep_plan.classified([read("a@example.test", bulk=True)], NO_RULES)
    assert (got["bucket"], got["conf"], got["reason"]) == (MKT, "high", "List-Unsubscribe header")
    assert got["uid"] == "1"


def test_a_rule_is_heard_on_what_is_read():
    [got] = sweep_plan.classified([read("a@example.test", bulk=True)], rules(keep_senders=["a@example.test"]))
    assert got["bucket"] == PROTECTED


def test_a_sweep_takes_only_junk():
    rows = [{**read("a@example.test"), "bucket": b} for b in (MKT, BLOCKED, PERSONAL, PROTECTED)]
    assert [r["bucket"] for r in sweep_plan.junk(rows)] == [MKT, BLOCKED]


# ── a stored row, judged as a sweep would ─────────────────────────────────────
def test_a_stored_row_goes_by_its_lower_cased_sender():
    assert sweep_plan.stored_bucket(stored("Ada@Example.test"), NO_RULES)[1] == "ada@example.test"
    assert sweep_plan.stored_bucket(stored(None), NO_RULES)[1] == ""


@pytest.mark.parametrize("row", [stored("a@example.test", bucket=MKT), stored("a@example.test", list_unsub="<https://list.example.test/u>")])
def test_what_ingest_saw_of_its_list_headers_shows(row):
    assert sweep_plan.stored_bucket(row, NO_RULES)[0] == MKT


def test_with_no_sign_of_a_list_it_is_no_newsletter():
    assert sweep_plan.stored_bucket(stored("a@example.test"), NO_RULES)[0] == PERSONAL


def test_a_rule_made_since_still_wins():
    assert sweep_plan.stored_bucket(stored("a@example.test", bucket=MKT), rules(keep_senders=["a@example.test"]))[0] == PROTECTED


def test_a_stored_row_with_nothing_filled_in_is_still_judged():
    assert sweep_plan.stored_bucket(stored("a@example.test", subject=None, name=None), NO_RULES)[0] == PERSONAL


def test_a_stored_rows_subject_counts():
    assert sweep_plan.stored_bucket(stored("a@example.test", subject="Your receipt"), NO_RULES)[0] == classifier.RECEIPT


# ── an AI sweep ───────────────────────────────────────────────────────────────
def test_an_ai_sweep_takes_what_was_called_junk_by_message_id():
    called, other = read("a@example.test", mid="<j@example.test>"), read("a@example.test", mid="<k@example.test>")
    assert sweep_plan.ai_targets([called, other], {"<j@example.test>"}, NO_RULES) == ([called], 0)


def test_mail_with_no_message_id_is_out_of_an_ai_sweeps_reach():
    assert sweep_plan.ai_targets([read("a@example.test", mid="")], {""}, NO_RULES) == ([], 0)


def test_a_keep_rule_holds_an_ai_sweep_back_and_is_counted():
    live = [read("kept@example.test", mid="<j@example.test>"), read("a@kept.example.test", mid="<j@example.test>"),
            read("other@example.test", mid="<j@example.test>")]
    targets, kept = sweep_plan.ai_targets(live, {"<j@example.test>"}, rules(keep_senders=["kept@example.test"], keep_domains=["kept.example.test"]))
    assert ([t["from"] for t in targets], kept) == (["other@example.test"], 2)


def test_the_audit_says_an_ai_verdict_took_it():
    assert sweep_plan.ai_audited([read("a@example.test", uid="9", subject="Offer")]) == [
        {"uid": "9", "from": "a@example.test", "subject": "Offer", "bucket": "ai-junk", "reason": "is_junk (AI verdict)"}]
