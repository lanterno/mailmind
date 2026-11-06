"""Behavioral tests for the mail classifier + learned-rules mechanism.

Hermetic: the rules layer is stubbed with a synthetic fixture so the suite
passes in a clean clone (where mailmind_rules.json is absent) and never depends
on any real account, employer, or contact. All addresses below are fictional.

Run:  python3 tests/unit/curation/script_classifier.py      (exits non-zero on any failure)
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, ROOT)

from mailmind.curation.domain import classifier as m

# Stub the learned rules with a synthetic fixture (tests the MECHANISM only).
RULES = {
    "keep_senders": set(), "block_senders": set(),
    "keep_domains": {"work-example.test"}, "block_domains": {"spam-platform.test"},
}

# (from_addr, subject, has_list_unsub, expected_bucket, note)
CASES = [
    # --- learned rules win first ---
    ("teamlead@work-example.test", "Re: the roadmap", False, m.PROTECTED, "kept by rule (role/thread ignored)"),
    ("billing@work-example.test", "Invoice #42", False, m.PROTECTED, "kept by rule beats receipt"),
    ("noreply@spam-platform.test", "anything at all", False, m.BLOCKED, "blocked by rule"),
    ("x@mail.spam-platform.test", "report", False, m.BLOCKED, "blocked domain matches sub-domain"),

    # --- bulk caught via robust signals (the leak the classifier closes) ---
    ("info@email.eventsco.example", "New events near you", False, m.MKT, "bulk sub-domain email."),
    ("newsletters@somesite.org", "Daily reminder", False, m.MKT, "newsletters@ role"),
    ("digest@mail.bigbrand.example", "This week", False, m.MKT, "mail. sub-domain"),
    ("namaste@studio.example", "Class schedule", True, m.MKT, "List-Unsubscribe header"),
    ("promo@store.example", "50% off", False, m.MKT, "marketing role"),
    ("x@sendgrid.net", "anything", False, m.MKT, "ESP infrastructure domain"),

    # --- ambiguous roles KEPT without a corroborating bulk signal (work-mail safety) ---
    ("info@smallco.example", "New private message", False, m.PERSONAL, "weak role, no bulk signal -> keep"),
    ("hello@startup.example", "Re: our call", False, m.PERSONAL, "weak role + thread -> keep"),

    # --- spam precision: fraud yes, marketing/charity no ---
    ("scammer@gmail.com", "Turn $1.50 into a million!!!", False, m.SPAM, "money + !!! from free-mail"),
    ("fraudster@gmail.com", "CONTACT WESTERN UNION PAYMENT OFFICE", False, m.SPAM, "strong fraud phrase"),
    ("mlmguy@gmail.com", '" Matrixforall " Team Build', False, m.SPAM, "MLM from free-mail"),
    ("news@info.somecharity.org", "Crisis: 14 millions de personnes", False, m.MKT, "charity is NOT spam"),
    ("no-reply@send.somefintech.example", "You can now get paid globally", False, m.MKT, "corp marketing is NOT spam"),
    ("news@e.foo.example", "Looking for a Specialist", False, m.MKT, "no 'cialis' substring false-positive"),

    # --- receipts / transactional ---
    ("failed-payments@mail.acme.example", "your payment was unsuccessful", False, m.RECEIPT, "billing local-part"),
    ("invoice+statements@mail.acme.example", "Your receipt", False, m.RECEIPT, "transactional"),

    # --- protected institutional domains (generic) ---
    ("no-reply@impots.gouv.fr", "avis d'imposition", False, m.PROTECTED, "government .gouv"),
    ("prof@stanford.edu", "seminar invite", False, m.PROTECTED, "education .edu"),
    ("portal@myclinic-health.example", "your appointment", False, m.PROTECTED, "health keyword in domain"),

    # --- real people & threads ---
    ("alice.smith@gmail.com", "lunch tomorrow?", False, m.PERSONAL, "human on personal mailbox"),
    ("bob.jones@randomcorp.example", "Re: our meeting notes", False, m.PERSONAL, "reply thread kept over role heuristics"),

    # --- automated notifications ---
    ("no-reply@rideshare.example", "Your trip receipt is ready", False, m.RECEIPT, "transactional beats no-reply"),
    ("no-reply@widgets.example", "Your account activity", False, m.NOTIF, "no-reply notification"),
]


def run():
    passed = failed = 0
    for frm, subj, lu, expected, note in CASES:
        got = m.classify(frm, subj, "", lu, rules=RULES)[0]
        if got == expected:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL  {frm:40} expected {expected:<22} got {got:<22} ({note})")
    # invariants
    assert m.is_junk(m.BLOCKED) and m.is_junk(m.SPAM) and m.is_junk(m.MKT) and m.is_junk(m.NOTIF)
    assert not m.is_junk(m.PROTECTED) and not m.is_junk(m.RECEIPT) and not m.is_junk(m.PERSONAL)
    # non-ASCII subjects must not crash
    m.classify("user@example.com", "新年快乐 🎉 здравей", "", False, rules=RULES)
    print(f"\n{passed} passed, {failed} failed (of {len(CASES)})")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
