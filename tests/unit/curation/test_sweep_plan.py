"""What a sweep would take, with no server, no database and no I/O. Moving it is tests/e2e/test_cli.py and test_http_cleanup."""
import pytest

from mailmind.curation.domain import classifier, rulebook, sweep_plan
from mailmind.curation.domain.classifier import BLOCKED, JUNK_BUCKETS, MKT, NOTIF, PERSONAL, PROTECTED, SPAM
from mailmind.curation.domain.live import READ_FIELDS, held, read_from_headers

NO_RULES = rulebook.as_sets(rulebook.empty())
A = "ada@example.test"


def rules(**lists):
    return {**NO_RULES, **{k: frozenset(v) for k, v in lists.items()}}


def read(sender, mid="<a@example.test>", subject="Hello", name="", bulk=False, uid="1"):
    return {"uid": uid, "from": sender, "name": name, "subject": subject, "mid": mid, "bulk": bulk, "starred": False,
            "unread": False}


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
    assert read_from_headers(7, raw, {"\\Seen"}) == {"uid": "7", "from": "ada@example.test", "name": "Ada",
                                                      "subject": "Hello", "mid": "<m@example.test>", "bulk": False,
                                                      "starred": False, "unread": False}


def test_a_plan_names_the_senders_with_the_most_and_the_newest_subjects_of_each_bucket_that_goes():
    def junk(uid, who, subject, bucket=MKT):
        return {**read(f"{who}@loom.example.test", subject=subject, uid=str(uid)), "bucket": bucket, "conf": "high",
                "reason": "list"}
    rows = [junk(3, "no-reply", "alert", NOTIF), junk(10, "a", "spring"), junk(11, "c", "spring"), junk(12, "b", "sale"),
            junk(13, "d", "summer"), junk(14, "e", "autumn"), junk(15, "b", "weekly"), junk(16, "f", "weekly"),
            junk(17, "news", "issue 2"), junk(2, "news", "issue"), junk(4, "prize", "You won", SPAM)]

    def sender(who, n):
        return {"from_addr": f"{who}@loom.example.test", "count": n}
    assert sweep_plan.going(rows) == [
        {"bucket": SPAM, "count": 1, "senders": [sender("prize", 1)], "subjects": ["You won"]},
        {"bucket": MKT, "count": 9,
         "senders": [sender("b", 2), sender("news", 2), sender("a", 1), sender("c", 1), sender("d", 1)],
         "subjects": ["issue 2", "weekly", "autumn", "summer", "sale"]},
        {"bucket": NOTIF, "count": 1, "senders": [sender("no-reply", 1)], "subjects": ["alert"]}]
    assert sweep_plan.going([]) == []


def test_a_sweep_takes_the_newest_of_its_junk_up_to_its_cap_in_inbox_order():
    junk = [{**read("news@loom.example.test", uid=str(uid)), "bucket": "newsletters/marketing", "conf": "high",
             "reason": "list"}
            for uid in (7, 3, *range(10, 10 + sweep_plan.SWEEP_MAX))]
    taken, capped = sweep_plan.within_cap(junk)
    assert capped and [r["uid"] for r in taken] == [str(u) for u in range(10, 10 + sweep_plan.SWEEP_MAX)]
    assert sweep_plan.within_cap(junk[:sweep_plan.SWEEP_MAX]) == (junk[:sweep_plan.SWEEP_MAX], False)


@pytest.mark.parametrize("flags, starred, unread", [({"\\Seen"}, False, False), ({"\\seen", "\\Answered"}, False, False),
                                                    ({"\\Seen", "\\FLAGGED"}, True, False), ({"\\Flagged"}, True, True),
                                                    (set(), False, True)])
def test_a_star_or_no_seen_flag_holds_a_message_back_whatever_the_case(flags, starred, unread):
    got = read_from_headers(1, b"From: a@example.test\r\n\r\n", flags)
    assert (got["starred"], got["unread"], held(got)) == (starred, unread, starred or unread)


@pytest.mark.parametrize("header, bulk", [(b"List-Unsubscribe: <https://list.example.test/u>\r\n", True),
                                          (b"List-ID: <news.example.test>\r\n", True), (b"X-Other: 1\r\n", False)])
def test_either_list_header_says_it_is_bulk(header, bulk):
    assert read_from_headers(1, b"From: a@example.test\r\n" + header + b"\r\n", ())["bulk"] is bulk


def test_a_sweep_asks_the_server_for_what_it_reads():
    assert {"FROM", "SUBJECT", "MESSAGE-ID", "LIST-UNSUBSCRIBE", "LIST-ID"} == set(READ_FIELDS)


def test_a_message_with_no_id_reads_as_none():
    assert read_from_headers(1, b"From: a@example.test\r\n\r\n", ())["mid"] == ""


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


def kept(uid, sender, subject="Hello", bucket=PERSONAL, mid=None, seen=1, flagged=0, **cols):
    """A stored INBOX row as a sweep's preview reads it."""
    return {**stored(sender, bucket=bucket, subject=subject, **cols), "uid": uid, "message_id": mid, "seen": seen,
            "flagged": flagged}


def test_a_stored_row_reads_as_a_sweep_reads_one_off_the_server():
    """As the Rust server reads it (core/crates/mm-curation/tests/sweeping.rs)."""
    row = kept(7, "News@Loom.example.test", "Issue 218", mid=" <issue-218@loom.example.test> ", name="The Weekly Loom")
    assert sweep_plan.read_from_stored(row) == {"uid": "7", "from": "news@loom.example.test", "name": "The Weekly Loom",
                                                "subject": "Issue 218", "mid": "<issue-218@loom.example.test>",
                                                "bulk": False, "starred": False, "unread": False}
    assert sweep_plan.read_from_stored({**row, "bucket": MKT})["bulk"]
    assert sweep_plan.read_from_stored({**row, "list_unsub_raw": "<https://loom.example.test/u>"})["bulk"]
    starred = sweep_plan.read_from_stored({**row, "flagged": 1})
    assert (starred["starred"], held(starred)) == (True, True)
    unseen = sweep_plan.read_from_stored(kept(8, None, subject=None, seen=None))
    assert (unseen["unread"], held(unseen), unseen["mid"], unseen["from"], unseen["subject"]) == (True, True, "", "", "")


def test_a_plan_from_stored_rows_answers_as_one_read_off_the_server():
    """The same three messages, stored or read live: Lin's lunch, Loom's issue and a build, each read."""
    stored_rows = [kept(1, "lin@example.test", "Lunch", mid="<l@example.test>"),
                   kept(2, "news@loom.example.test", "Issue", mid="<i@example.test>", list_unsub="<https://loom.example.test/u>"),
                   kept(3, "no-reply@ci.example.test", "Build", mid="<b@example.test>")]
    live = [read("lin@example.test", "<l@example.test>", "Lunch", uid="1"),
            read("news@loom.example.test", "<i@example.test>", "Issue", bulk=True, uid="2"),
            read("no-reply@ci.example.test", "<b@example.test>", "Build", uid="3")]
    from_store = sweep_plan.planned(A, sweep_plan.classified([sweep_plan.read_from_stored(r) for r in stored_rows], NO_RULES))
    assert from_store == sweep_plan.planned(A, sweep_plan.classified(live, NO_RULES))
    answer, taken = from_store
    assert ([r["uid"] for r in taken], answer["planned"], answer["held_back"], answer["no_undo"]) == (["2", "3"], 2, 0, 0)


def test_a_plan_holds_back_starred_or_unread_junk_and_takes_the_newest_of_the_rest_up_to_the_cap():
    rows = [{**read("news@loom.example.test", mid="", uid=str(uid)), "bucket": MKT, "conf": "high", "reason": "list"}
            for uid in range(1, sweep_plan.SWEEP_MAX + 4)]
    rows[0]["starred"], rows[1]["unread"] = True, True
    rows += [{**read("lin@example.test", uid="900"), "bucket": PERSONAL, "conf": "high", "reason": "person"}]
    answer, taken = sweep_plan.planned(A, rows)
    assert (answer["planned"], answer["held_back"], answer["capped"], answer["no_undo"]) == (
        sweep_plan.SWEEP_MAX, 2, True, sweep_plan.SWEEP_MAX)
    assert [r["uid"] for r in taken] == [str(u) for u in range(4, sweep_plan.SWEEP_MAX + 4)]
    assert answer["by_bucket"] == {MKT: sweep_plan.SWEEP_MAX + 3, PERSONAL: 1}
    answer, taken = sweep_plan.planned(A, rows[2:12])
    assert (answer["planned"], answer["capped"], answer["moved"], answer["stragglers"], answer["error"]) == (
        10, False, 0, [], None)


# ── a sweep of stored junk ───────────────────────────────────────────────────────────────
def test_only_stored_junk_someone_read_and_no_one_starred_is_sweepable():
    """As the Rust server counts it (core/crates/mm-curation/tests/sweep_plan.rs)."""
    rows = [{"seen": 1, "flagged": 0}, {"seen": 1, "flagged": 0}, {"seen": 0, "flagged": 0}, {"seen": 1, "flagged": 1},
            {"seen": 1, "flagged": 0}]
    judged = [(MKT, "news@loom.example.test")] * 4 + [(PERSONAL, "lin@example.test")]
    assert sweep_plan.sweepable(rows, judged) == 2


def test_an_ai_sweep_takes_what_was_called_junk_by_message_id():
    called, other = read("a@example.test", mid="<j@example.test>"), read("a@example.test", mid="<k@example.test>")
    assert sweep_plan.stored_targets([called, other], {"<j@example.test>"}, NO_RULES) == ([called], 0)


def test_mail_with_no_message_id_is_out_of_an_ai_sweeps_reach():
    assert sweep_plan.stored_targets([read("a@example.test", mid="")], {""}, NO_RULES) == ([], 0)


def test_a_keep_rule_holds_an_ai_sweep_back_and_is_counted():
    live = [read("kept@example.test", mid="<j@example.test>"), read("a@kept.example.test", mid="<j@example.test>"),
            read("other@example.test", mid="<j@example.test>")]
    targets, kept = sweep_plan.stored_targets(live, {"<j@example.test>"}, rules(keep_senders=["kept@example.test"], keep_domains=["kept.example.test"]))
    assert ([t["from"] for t in targets], kept) == (["other@example.test"], 2)


def test_the_audit_says_an_ai_verdict_took_it():
    assert sweep_plan.stored_audited([read("a@example.test", uid="9", subject="Offer")]) == [
        {"uid": "9", "from": "a@example.test", "subject": "Offer", "bucket": "stored-junk", "reason": "is_junk (the classifier, at ingest)"}]
