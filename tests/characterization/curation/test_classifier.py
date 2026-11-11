"""The classifier: which verdict wins, what counts as a bulk domain, and when a rule change reaches mail."""
import json

import pytest

from fixtures import world as w
from mailmind.bootstrap import curation
from stored_rows import put_document

RULES = {"keep_senders": ["both@rules.example.test"],
         "block_senders": ["both@rules.example.test", "noreply@kept.example.test"],
         "keep_domains": ["kept.example.test", "dual.example.test"],
         "block_domains": ["dual.example.test", "blocked.example.test"]}

PRECEDENCE = [
    # (from, subject, List-Unsubscribe/List-ID, bucket, confidence)
    ("both@rules.example.test", "hi", False, "personal/protected", "high"),        # keep sender beats block sender
    ("noreply@kept.example.test", "hi", False, "blocked", "high"),                 # block sender beats keep domain
    ("x@dual.example.test", "hi", False, "personal/protected", "high"),            # keep domain beats block domain
    ("x@sub.kept.example.test", "You have won the lottery", False, "personal/protected", "high"),  # rules beat scam
    ("winner@blocked.example.test", "hi", False, "blocked", "high"),
    ("records@example.gov", "You have won the lottery", False, "spam/scam", "high"),   # scam beats protected
    ("ops@bank.example.test", "Your invoice", False, "personal/protected", "high"),  # protected beats receipt
    ("news@bank.example.test", "Rates", False, "newsletters/marketing", "med"),      # a marketing role loses it
    ("billing@studio.example.test", "hi", True, "receipts/transactional", "med"),    # receipt beats List-Unsubscribe
    ("lin@studio.example.test", "Re: lunch", True, "newsletters/marketing", "high"),  # list header beats a reply
    ("lin.example@gmail.com", "Re: [weave] patterns", True, "newsletters/marketing", "high"),
    ("lin@sendgrid.example.test", "Re: lunch", False, "newsletters/marketing", "high"),  # ESP beats a reply
    ("lin@e.studio.example.test", "Re: lunch", False, "newsletters/marketing", "med"),   # bulk sub-domain too
    ("marketing@studio.example.test", "Re: your quote", False, "other/personal-ish", "high"),  # reply beats role
    ("no-reply@studio.example.test", "Re: your quote", False, "automated-notifications", "med"),
    ("lin@studio.example.test", "AW: Termin", False, "other/personal-ish", "high"),
    ("lin@studio.example.test", "WG: Termin", False, "other/personal-ish", "high"),
    ("lin@studio.example.test", "TR: devis", False, "other/personal-ish", "high"),
    ("lin@studio.example.test", "RV: cita", False, "other/personal-ish", "high"),
    ("lin@studio.example.test", "R\u00e9f: devis", False, "other/personal-ish", "high"),
    ("lin@studio.example.test", "Fw: notes", False, "other/personal-ish", "high"),
    ("marketing@studio.example.test", "Spring range", False, "newsletters/marketing", "med"),
    ("news@gmail.com", "hello", False, "other/personal-ish", "low"),               # a role on free mail: not bulk
    ("alerts@studio.example.test", "Login", False, "automated-notifications", "med"),
    ("lin.example@gmail.com", "hi", False, "other/personal-ish", "med"),
    ("lin@studio.example.test", "hi", False, "other/personal-ish", "low"),
    ("make.example@gmail.com", "make money now", False, "spam/scam", "high"),       # weak scam from free mail
    ("deals@example.tk", "make money now", False, "spam/scam", "high"),             # or a junk TLD
    ("lin@studio.example.test", "make money now", False, "other/personal-ish", "low"),  # but not from elsewhere
]


@pytest.fixture
def rule_book(data_dir):
    put_document("rules", json.dumps(RULES))


@pytest.mark.parametrize("addr,subject,listed,bucket,conf", PRECEDENCE, ids=[f"{a} {s}" for a, s, *_ in PRECEDENCE])
def test_hwf_classifier_precedence(rule_book, addr, subject, listed, bucket, conf):
    assert curation.classify(addr, subject, "", listed)[:2] == (bucket, conf)


@pytest.mark.parametrize("addr", ["lin@msg.example.test", "ivo@nsg.example.test", "lin@studio.sg"])
def test_hwf_a_domain_merely_containing_an_esp_marker_is_not_bulk(data_dir, addr):
    assert curation.classify(addr, "Lunch on Thursday?", "", False)[0] != "newsletters/marketing"


@pytest.mark.parametrize("addr", ["deals@sg.example.test", "deals@em.sg.example.test"])
def test_hwf_the_short_esp_marker_still_counts_as_a_whole_label(data_dir, addr):
    assert curation.classify(addr, "Lunch on Thursday?", "", False)[:3] == (
        "newsletters/marketing", "high", "bulk email-service-provider domain")


def test_hwf_the_classifier_sees_rule_edits_and_reads_any_case(data_dir):
    from mailmind.bootstrap.curation import rules
    put_document("rules", json.dumps({"keep_senders": ["Lin@Studio.Example.Test"]}))
    assert curation.classify("lin@studio.example.test", "hi")[0] == "personal/protected"
    assert rules.load()["keep_senders"] == ["lin@studio.example.test"]
    put_document("rules", json.dumps({"block_domains": ["STUDIO.example.test"]}))     # a version on, which the reader asks
    assert curation.classify("lin@studio.example.test", "hi")[0] == "blocked"


def test_hwf_a_rule_change_reaches_stored_mail_only_when_it_is_stored_again(world):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    from mailmind.bootstrap.curation import rules
    rules.add("lin@example.test", "block")
    assert world.row("ada-inbox-01-lunch.eml")["bucket"] == "other/personal-ish"
    ingest.sync_folder(accounts.get(w.ADA), "INBOX", full=True)
    assert world.row("ada-inbox-01-lunch.eml")["bucket"] == "blocked"
    assert world.row("ada-archive-01-old-thread.eml")["bucket"] == "other/personal-ish"
