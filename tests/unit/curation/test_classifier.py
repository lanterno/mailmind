"""Behavioural tests for the mail classifier + learned-rules mechanism.

The rules layer is a synthetic fixture, so this never depends on any real account, employer or
contact. All addresses below are fictional; the free-mail, .edu, .gouv and ESP domains among them
are the rules under test."""
import pytest

from mailmind.curation.domain import classifier as m

# A synthetic rule book (tests the MECHANISM only).
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
    ("no-reply@shop.example", "Your Order # 123", False, m.RECEIPT, "an order number written with a space"),
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


@pytest.mark.parametrize("frm, subject, list_unsub, expected", [c[:4] for c in CASES], ids=[c[4] for c in CASES])
def test_classify(frm, subject, list_unsub, expected):
    assert m.classify(frm, subject, "", list_unsub, rules=RULES)[0] == expected


@pytest.mark.parametrize("bucket, junk", [(m.BLOCKED, True), (m.SPAM, True), (m.MKT, True), (m.NOTIF, True),
                                          (m.PROTECTED, False), (m.RECEIPT, False), (m.PERSONAL, False)])
def test_is_junk(bucket, junk):
    assert bool(m.is_junk(bucket)) is junk


def test_a_non_ascii_subject_does_not_crash():
    assert m.classify("user@example.test", "新年快乐 🎉 здравей", "", False, rules=RULES)[0] == m.PERSONAL


# Patterns that matched inside longer words.
@pytest.mark.parametrize("frm", ["noreply@medium.example", "alerts@mediaplayer.example"])
def test_a_media_sender_is_not_protected_as_medical(frm):
    assert m.classify(frm, "Your weekly digest", "", False, rules=RULES)[0] != m.PROTECTED


@pytest.mark.parametrize("frm", ["noreply@medical-centre.example", "alerts@medicare.example", "x@medi.example",
                                 "noreply@my-medic.example"])
def test_a_medical_sender_is_still_protected(frm):
    assert m.classify(frm, "Your weekly digest", "", False, rules=RULES)[0] == m.PROTECTED


@pytest.mark.parametrize("frm", ["offers@grand-hospitality.example", "hello@revolutionbeauty.example"])
def test_a_word_that_only_starts_like_a_protected_one_is_not_protected(frm):
    assert m.classify(frm, "Our news", "", False, rules=RULES)[0] != m.PROTECTED


@pytest.mark.parametrize("frm", ["portal@cityhospital.example", "portal@hospitals.example", "hello@revolut.com",
                                 "hello@email.revolut.com"])
def test_a_hospital_and_revolut_still_are(frm):
    assert m.classify(frm, "Our news", "", False, rules=RULES)[0] == m.PROTECTED


@pytest.mark.parametrize("frm", ["hello@likewise.com", "hello@pinstripe.com", "hello@wise.company.example"])
def test_a_domain_that_only_ends_like_a_protected_one_is_not_protected(frm):
    assert m.classify(frm, "Our news", "", False, rules=RULES)[0] != m.PROTECTED


@pytest.mark.parametrize("frm", ["hello@wise.com", "hello@mail.wise.com", "hello@wise.com.example", "hello@stripe.com",
                                 "hello@e.stripe.com"])
def test_a_protected_domain_and_its_sub_domains_still_are(frm):
    assert m.classify(frm, "Our news", "", False, rules=RULES)[0] == m.PROTECTED


@pytest.mark.parametrize("subject", ["Join our Matrix room", "Selling my bike to a recycler"])
def test_a_person_writing_an_ordinary_word_is_not_a_scam(subject):
    assert m.classify("sam@gmail.com", subject, "", False, rules=RULES)[0] == m.PERSONAL


@pytest.mark.parametrize("subject", ["Join MatrixForAll today", "The 2x2 CashMatrix pays", "Instant Cycler opens",
                                     "cyclers pay daily"])
def test_a_scheme_named_for_a_matrix_or_a_cycler_still_is(subject):
    assert m.classify("sam@gmail.com", subject, "", False, rules=RULES)[0] == m.SPAM


@pytest.mark.parametrize("subject", ["The budget paid for the trip", "Learn 5 chords tonight", "Our nonprofit system is down",
                                     "Maybe your own boss can sign it", "The ongoing viral trend", "Urgent: my refund",
                                     "Parking is $40 per weekend", "I network from home on Fridays"])
def test_a_phrase_inside_a_persons_word_is_not_a_scam(subject):
    assert m.classify("sam@gmail.com", subject, "", False, rules=RULES)[0] in (m.PERSONAL, m.RECEIPT)


@pytest.mark.parametrize("subject", ["Get paid to click", "Earn $500 today", "Be your own boss!", "URGENT fund transfer",
                                     "$40 per week, guaranteed", "Our profit system", "Going viral: join asap"])
def test_a_scheme_still_is(subject):
    assert m.classify("sam@gmail.com", subject, "", False, rules=RULES)[0] == m.SPAM
