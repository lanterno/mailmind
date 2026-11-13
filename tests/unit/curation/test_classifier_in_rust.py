"""The classifier, now the Rust core's, holds to what the Python app's classifier answered before it: the same bucket,
confidence and reason for the same message. Ingest stores them, and a rule change classifies stored mail again, so an
answer that drifted would move mail a person never touched.

The Python classifier is kept below as it was, as the oracle, changed only where the core fixed a bug since, each change
saying so. core/crates/mm-curation/tests/classifier.rs pins a few of its answers on the Rust side. Python's `str` and `re` read Unicode their own way (word characters, whitespace,
digits, lower-casing, ignored case), so besides the generated messages, the tests at the end go through every
character there is.
"""
import re
import string

import mailmind_leaves
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mailmind.bootstrap import curation
from mailmind.curation.domain import classifier, rulebook
from mailmind.curation.domain.rulebook import KEYS, domain_listed
from stored_rows import put_document

# ── the oracle: mailmind/curation/domain/classifier.py as it was ──────────────────────────────────────────────────

SPAM = "spam/scam"
BLOCKED = "blocked"                     # user said "always trash this sender/domain"
MKT = "newsletters/marketing"
NOTIF = "automated-notifications"
RECEIPT = "receipts/transactional"
PROTECTED = "personal/protected"
PERSONAL = "other/personal-ish"

JUNK_BUCKETS = {SPAM, BLOCKED, MKT, NOTIF}

# --- sender-side signal vocabularies -----------------------------------------
FREE_MAIL = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.co.uk", "outlook.com",
    "live.com", "live.fr", "msn.com", "yahoo.com", "yahoo.co.uk", "yahoo.fr",
    "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com", "gmx.com",
    "gmx.de", "gmx.net", "web.de", "proton.me", "protonmail.com", "pm.me",
    "hey.com", "fastmail.com", "zoho.com", "mail.com", "libero.it", "orange.fr",
    "free.fr", "qq.com", "163.com", "naver.com",
}

# UNAMBIGUOUS marketing local-parts: classify as bulk on their own.
ROLE_MKT = {
    "news", "newsletter", "newsletters", "marketing", "promo", "promotions",
    "promotion", "offers", "deals", "digest", "campaigns", "campaign", "buzz",
    "magazine", "mailings", "mailing", "enews", "discover", "inspiration",
}
# AMBIGUOUS small-business roles: often bulk, but also real small teams. Only
# treated as marketing when a corroborating bulk signal is also present.
ROLE_MKT_WEAK = {
    "info", "hello", "hi", "hey", "contact", "team", "social", "community",
    "members", "engage", "updates", "stories", "weekly",
}
# UNAMBIGUOUS machine senders.
ROLE_NOTIF = {
    "no-reply", "noreply", "no_reply", "donotreply", "do-not-reply", "do_not_reply",
    "notification", "notifications", "notify", "alerts", "alert", "automated",
    "mailer-daemon", "mailer", "bounce", "postmaster", "mailbot",
    "auto-confirm", "autoconfirm",
}
ROLE_TXN = {
    "receipt", "receipts", "invoice", "invoices", "billing", "invoicing",
    "payment", "payments", "orders", "order", "statements", "accounting",
}

# Email-service-provider / bulk-sending infrastructure (substring match on domain)
ESP_MARKERS = (
    "sendgrid", "sparkpost", "mailgun", "mcsv.net", "mcdlv.net", "rsgsv.net",
    "list-manage", "mailchimp", "beehiiv", "substack", "ghost.io", "hubspot",
    "klaviyo", "sendinblue", "brevo", "amazonses", "sail thru", "sailthru",
    "iterable", "customer.io", "customeriomail", "braze", "mandrillapp",
    "cmail19", "cmail20", "createsend", "exct.net", "responsys", "marketo",
    "pardot", "salesforce", "constantcontact", "ccsend", "icontact", "mailjet",
    "postmarkapp", "sparkpostmail", "everestengagement", "convertkit",
    "ck.page", "drip.com", "activecampaign", "getresponse", "aweber", "emarsys",
    "bluecore", "cordial", "movableink", "narvar", "shopifyemail",
)
# ESP markers too short to match inside a word: each must be a whole dot-label, and not the last
# one (.sg is Singapore's top-level domain)
ESP_LABELS = {"sg"}
# first dot-label of the domain that signals a bulk stream
BULK_SUBDOMAINS = {
    "e", "em", "email", "emails", "mail", "mailing", "mailings", "news",
    "newsletter", "newsletters", "t", "m", "send", "sender", "go", "link",
    "links", "updates", "mktg", "marketing", "click", "clicks", "et", "trx",
    "message", "messages", "notif", "notification", "notifications", "ml",
    "campaign", "campaigns", "info", "hello", "reply", "comms", "communication",
    "communications", "list", "lists", "engage", "p", "e1",
}
SUSPICIOUS_TLDS = {
    "tk", "ml", "ga", "cf", "gq", "work", "click", "link", "loan", "kim",
    "country", "stream", "download", "racing", "win", "bid", "men",
}

# --- subject-side signal vocabularies ----------------------------------------
TRANSACTIONAL = re.compile(
    r"\b(receipt|invoice|payment received|your payment|you paid|amount paid|"
    r"your bill|e-?statement|statement is|e-?ticket|boarding pass|itinerary|"
    r"booking (confirm|reference|is)|reservation confirm|order (confirm|#|no\.?|placed|shipped|dispatched)|"
    r"has shipped|out for delivery|refund|tax invoice|\bvat\b|charged|transaction (receipt|confirm))\b",
    re.I)

# Unambiguous fraud: fires regardless of who sent it.
STRONG_SCAM = re.compile(
    r"(you('| ?ha)ve won|you are a winner|claim your (prize|reward|winnings|funds)|"
    r"western union|money ?gram|wire transfer to|inheritance|next of kin|beneficiar|"
    r"lottery|jackpot|unclaimed (funds|money)|nigerian prince|"
    r"\b(viagra|cialis|penis enlarge\w*)\b|"
    r"double your (money|btc|bitcoin|investment))",
    re.I)
# Money-making / MLM / HYIP / urgency: only counts when the SENDER is sketchy
# (free webmail or a junk TLD). Legit brands use this language in marketing too.
WEAK_SCAM = re.compile(
    r"(get paid|getpaid|make money|earn money|earn \$?\d|make \$?\d|"
    r"\$\d[\d,]*\s*(a|per|/)\s*(day|week|month)|[0-9]{2,}\s*% (per|a) (day|month|return)|"
    r"work from home|passive income|be your own boss|financial freedom|"
    r"revenue share|traffic exchange|paid to click|\bptc\b|downline|matrix|cycler|"
    r"\bm2m\b|cash ?club|instant ?cash|join asap|going viral|pre-?launch|profit system|"
    r"risk[- ]free.{0,20}(invest|profit|return)|100% (guarantee|profit)|"
    r"urgent.{0,15}(transfer|fund))",
    re.I)
SHOUTY = re.compile(r"[A-Z]{6,}")
MONEY = re.compile(r"(\$\s?\d|\d\s?\$|usd|\bbtc\b|bitcoin|\beuros?\b|€|\bcash\b|\bprofit\b|million)", re.I)

# --- protected (never auto-trash) --------------------------------------------
# Generic institutional matchers. These are deliberately broad: protect your
# OWN niche institutions (a specific university, bank, clinic, ...) by adding
# their domains to keep_domains (`python -m mailmind rules keep <domain>`) rather than editing here.
GOV_RE = re.compile(r"(\.gov(\.[a-z]{2})?$|\.gouv\.[a-z]{2}$|\.go\.[a-z]{2}$|\.gob\.[a-z]{2}$|"
                    r"\.admin\.[a-z]{2}$|\.gc\.ca$|\.govt\.|\bgov\.)", re.I)
EDU_RE = re.compile(r"(\.edu$|\.edu\.[a-z]{2}$|\.ac\.[a-z]{2}$|\.uni-[a-z]+\.)", re.I)
# Fixed since: `\bmedi` took media and medium for medical.
HEALTH_RE = re.compile(r"(sante|\bsanté|health|clinic|hospital|\bmedi(?!a|um)|patient|doctolib|dentist)", re.I)
# Fixed since: `wise\.com` matched likewise.com, and `stripe\.com$` pinstripe.com.
FINANCE_RE = re.compile(r"(\bbank\b|\bbanque\b|paypal|(^|\.)wise\.com(\.|$)|revolut|\bn26\b|"
                        r"(^|\.)stripe\.com$|\bvisa\b|mastercard)", re.I)


def _split(addr: str | None) -> tuple[str, str]:
    addr = (addr or "").strip().lower()
    if "@" not in addr:
        return "", ""
    local, _, domain = addr.partition("@")
    return local, domain


def _is_protected_domain(domain: str, local: str) -> bool:
    if GOV_RE.search(domain) or EDU_RE.search(domain):
        return True
    # health / finance: protect unless it's an obvious marketing stream
    if (HEALTH_RE.search(domain) or FINANCE_RE.search(domain)) and local not in ROLE_MKT:
        return True
    return False


def python_looks_human(local: str | None) -> bool:
    """A local-part that looks like a person, not a role or a random mailer id."""
    if not local or local in ROLE_MKT or local in ROLE_MKT_WEAK or local in ROLE_NOTIF or local in ROLE_TXN:
        return False
    if any(local.startswith(p) for p in ("no-reply", "noreply", "no_reply", "donotreply")):
        return False
    if re.search(r"(news|newsletter|notif|mailer|mktg|marketing|campaign|bounce)", local):
        return False
    # long hex / random token locals are machine senders, not people
    if re.fullmatch(r"[0-9a-f]{12,}", local) or re.fullmatch(r"[a-z0-9]{18,}", local):
        return False
    if sum(c.isdigit() for c in local) > len(local) * 0.5:
        return False
    return bool(re.search(r"[a-z]", local))


def _is_scam(local: str, domain: str, subject: str, name: str) -> bool:
    tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
    blob = f"{subject} {name}"
    if STRONG_SCAM.search(blob):
        return True
    sketchy = domain in FREE_MAIL or tld in SUSPICIOUS_TLDS
    if sketchy and WEAK_SCAM.search(blob):
        return True
    # sketchy sender + money + loud styling (!!! or SHOUTING): classic blast
    if sketchy and MONEY.search(subject) and (subject.count("!") >= 3 or SHOUTY.search(subject)):
        return True
    return False


def python_classify(from_addr: str, subject: str | None = "", from_name: str | None = "", has_list_unsub: bool = False, *,
                    rules) -> tuple[str, str, str]:
    """Return (bucket, confidence in {'high','med','low'}, reason). `rules` is the rule book's keep and
    block lists as sets."""
    subject = subject or ""
    name = from_name or ""
    local, domain = _split(from_addr)
    label0 = domain.split(".")[0] if domain else ""
    nlabels = domain.count(".") + 1 if domain else 0
    full = from_addr.strip().lower()

    # 0) learned overrides win over all heuristics: the user's explicit verdicts.
    #    Exact-sender rules beat domain rules (a blocked noreply@ overrides a
    #    kept whole-domain), and within each scope keep beats block.
    if full in rules["keep_senders"]:
        return PROTECTED, "high", "kept by rule (sender)"
    if full in rules["block_senders"]:
        return BLOCKED, "high", "blocked by rule (sender)"
    if domain_listed(domain, rules["keep_domains"]):
        return PROTECTED, "high", "kept by rule (domain)"
    if domain_listed(domain, rules["block_domains"]):
        return BLOCKED, "high", "blocked by rule (domain)"

    # 1) spam/scam wins over the remaining heuristics (incl. gmail-sent scams)
    if _is_scam(local, domain, subject, name):
        return SPAM, "high", "fraud/MLM pattern"

    # 2) protected senders: keep, never auto-trash (unless it was scam above)
    if _is_protected_domain(domain, local):
        return PROTECTED, "high", "government/education/medical/financial domain"

    # 3) genuine financial records (subject wording OR a billing-ish local-part,
    #    incl. things like failed-payments@ / invoice+statements@)
    if (TRANSACTIONAL.search(subject) or local in ROLE_TXN
            or any(t in local for t in ("receipt", "invoice", "billing", "payment", "statement"))):
        return RECEIPT, "med", "transactional/receipt signal"

    # 4) newsletters / marketing: the big leak we now close
    if has_list_unsub:
        return MKT, "high", "List-Unsubscribe header"
    if any(m in domain for m in ESP_MARKERS) or ESP_LABELS & set(domain.split(".")[:-1]):
        return MKT, "high", "bulk email-service-provider domain"
    if nlabels >= 3 and label0 in BULK_SUBDOMAINS:
        return MKT, "med", f"bulk sub-domain ({label0}.)"

    # A genuine Re:/Fwd: reply that survived the hard bulk checks above is a real
    # human thread: keep it rather than letting a role address downgrade it.
    if RE_FWD.match(subject) and local not in ROLE_NOTIF:
        return PERSONAL, "high", "reply/forward in an active thread"

    if domain not in FREE_MAIL and local in ROLE_MKT:
        return MKT, "med", f"marketing role address ({local}@)"

    # 5) automated notifications
    if domain not in FREE_MAIL and (local in ROLE_NOTIF or NOTIF_RE.search(local)):
        return NOTIF, "med", "no-reply / notification address"

    # 6) a real person from a personal mailbox
    if domain in FREE_MAIL and python_looks_human(local):
        return PERSONAL, "med", "person on a personal mailbox"

    # 7) genuinely ambiguous: keep, flag for a human glance
    return PERSONAL, "low", "unclassified \u2014 review"


NOTIF_RE = re.compile(r"(no-?reply|do-?not-?reply|notification|notify|mailer-daemon|automated|noreply)", re.I)
RE_FWD = re.compile(r"^\s*(re|fwd|fw|aw|wg|tr|rv|r[ée]f)\s*:", re.I)  # reply/forward prefixes (multi-lingual)


def python_by_rule(reason: str | None) -> bool:
    """Whether `reason`, as classify gave it, says one of the person's rules decided."""
    return "by rule" in (reason or "")


def python_is_junk(bucket_name: str | None) -> bool:
    return bucket_name in JUNK_BUCKETS


# ── what the generated messages are made of ───────────────────────────────────────────────────────────────────────

# Every character Python's str.isspace holds of (strip and `\s` go by it), and some that only look like one.
SPACES = "".join(c for c in map(chr, range(0x3001)) if c.isspace())
NOT_SPACES = "\u200b\u180e\ufeff\u2060"
# What Python's re.IGNORECASE matches a letter with besides its other case, and what str.lower treats apart.
TWINS = {"i": "I\u0130\u0131", "s": "S\u017f", "k": "K\u212a", "e": "EéÉ", "a": "A\u0391", "o": "O\u039f", "\u03c3": "\u03a3\u03c2"}
# Characters that sit on one side of a word boundary for Python and the other for the `regex` crate, and some others.
EDGES = "_\u0301\u0345\u203f\u200d\u00b2\u2460\u0663\u2160\u00a0\ua7ce\U00016ea0'.:-#$€%!/"

LOCALS = sorted(ROLE_MKT | ROLE_MKT_WEAK | ROLE_NOTIF | ROLE_TXN) + [
    "lin", "ada.lovelace", "failed-payments", "invoice+statements", "no-reply-bot", "jo.newsom", "lin2", "li22", "l\u00b2\u00b2",
    "ab\u2460\u2460\u2460", "élève", "\u0391\u03a3", "\u0130nfo", "not\u0131fy", "x", "0123456789abcdef", "abcdefghijklmnopqrstu"]
DOMAINS = sorted(FREE_MAIL) + [f"x.{m}.example.test" for m in ESP_MARKERS] + [
    f"{s}.shop.example.test" for s in BULK_SUBDOMAINS] + [f"example.{t}" for t in SUSPICIOUS_TLDS] + [
    "studio.sg", "sg.example.test", "em.sg.example.test", "msg.example.test", "tax.gov", "tax.gov.uk", "impots.gouv.fr",
    "x.go.jp", "x.gob.mx", "x.admin.ch", "canada.gc.ca", "x.govt.nz", "gov.example.test", "stanford.edu", "x.edu.au",
    "x.ac.uk", "x.uni-berlin.de", "myclinic-health.example", "santé.example", "medica.example", "bank.example.test",
    "banque.example", "n26.example", "paypal.example", "wise.com", "stripe.com", "visa.example", "mastercard.example",
    "example.test", "mail.example.test",
    # Domains that only look like a protected one's.
    "medium.example", "mediaplayer.example", "x.medi", "x.mediu", "mediumx.example", "likewise.com", "wise.company",
    "x.wise.com", "wise.com.au", "wise.comx", "pinstripe.com", "x.stripe.com", "stripe.com.example"]
# A piece of every pattern's alternatives, and of the words around them.
PHRASES = [
    "receipt", "invoice", "payment received", "your payment", "you paid", "amount paid", "your bill", "e-statement",
    "estatement", "statement is", "e-ticket", "boarding pass", "itinerary", "booking confirm", "booking reference",
    "booking is", "reservation confirm", "order confirm", "order #", "order no", "order no.", "order placed",
    "order shipped", "order dispatched", "has shipped", "out for delivery", "refund", "tax invoice", "vat", "charged",
    "transaction receipt", "transaction confirm", "you've won", "you have won", "youhave won", "you are a winner",
    "claim your prize", "claim your funds", "western union", "moneygram", "money gram", "wire transfer to",
    "inheritance", "next of kin", "beneficiary", "lottery", "jackpot", "unclaimed funds", "unclaimed money",
    "nigerian prince", "viagra", "cialis", "penis enlargement", "double your money", "double your btc", "get paid",
    "getpaid", "make money", "earn money", "earn $5", "earn 5", "make $5", "$5 a day", "$1,000 per month", "$5/week",
    "50 % per day", "20% a return", "work from home", "passive income", "be your own boss", "financial freedom",
    "revenue share", "traffic exchange", "paid to click", "ptc", "downline", "matrix", "cycler", "m2m", "cash club",
    "cashclub", "instant cash", "join asap", "going viral", "prelaunch", "pre-launch", "profit system", "risk-free",
    "risk free", "invest", "profit", "return", "100% guarantee", "100% profit", "urgent", "transfer", "fund", "$5", "5$",
    "$ 5", "usd", "btc", "bitcoin", "euro", "euros", "€", "cash", "million", "!!!", "!", "WINNER", "FREE MONEY", "Re:",
    "RE :", "Fwd:", "Fw:", "AW:", "WG:", "TR:", "RV:", "Réf:", "Ref:", "re", "lunch", "hi"]
SEPARATORS = ["", " ", "  ", "\t", "\n", "\r", "\x1c", "\u3000", "\u200b", ".", ",", ":", "-", "1", "a", *EDGES]


@st.composite
def recased(draw, text):
    """`text` with each character as written, in the other case, or as a twin Python's re.IGNORECASE matches it with."""
    out = []
    for c in text:
        how = draw(st.integers(0, 3))
        if how == 1:
            out.append(c.upper())
        elif how == 2 and c.lower() in TWINS:
            out.append(draw(st.sampled_from(TWINS[c.lower()])))
        else:
            out.append(c)
    return "".join(out)


def recased_from(words):
    return st.sampled_from(words).flatmap(recased)


separator = st.sampled_from(SEPARATORS) | st.characters(exclude_categories=["Cs"])
phrases = st.lists(st.tuples(separator, recased_from(PHRASES)), max_size=4).flatmap(
    lambda parts: separator.map(lambda end: "".join(s + p for s, p in parts) + end))
texts = (st.none() | st.just("") | st.text(max_size=30) | phrases
         | st.text(alphabet=SPACES + NOT_SPACES + EDGES + "".join(TWINS.values()) + "aZ1$!", max_size=12))
local_parts = (recased_from(LOCALS) | st.text(max_size=10) | st.text(alphabet="0123456789abcdef", min_size=10, max_size=14)
               | st.text(alphabet=string.ascii_lowercase + string.digits, min_size=16, max_size=20)
               | st.text(alphabet="0123456789\u00b2\u2460\u0663a", max_size=6))
domains = recased_from(DOMAINS) | st.text(max_size=12) | st.just("")
padding = st.text(alphabet=SPACES + NOT_SPACES, max_size=2)


@st.composite
def messages(draw):
    """(from_addr, subject, from_name, has_list_unsub, rules), with rules that often name this sender, its domain or a
    parent of it, as the address reads once Python stripped and lower-cased it."""
    at = draw(st.sampled_from(["@"] * 6 + ["", "@@", " @"]))
    from_addr = draw(padding) + draw(local_parts) + at + draw(domains) + draw(padding)
    full = from_addr.strip().lower()
    domain = full.partition("@")[2]
    named = [full, domain, "", *(domain.split(".", n)[-1] for n in range(1, domain.count(".") + 1))]
    entries = st.frozensets(st.sampled_from(named) | st.sampled_from(DOMAINS) | st.text(max_size=8), max_size=3)
    lists = {k: draw(st.just(frozenset()) | entries) for k in KEYS}
    return from_addr, draw(texts), draw(texts), draw(st.booleans()), lists


BUCKETS = (SPAM, BLOCKED, MKT, NOTIF, RECEIPT, PROTECTED, PERSONAL)
NO_RULES = rulebook.no_sets()


# ── the same answers ──────────────────────────────────────────────────────────────────────────────────────────────

@settings(max_examples=2000, deadline=None)
@given(messages())
def test_rust_classifies_every_message_as_python_did(message):
    from_addr, subject, name, listed, lists = message
    said = python_classify(from_addr, subject, name, listed, rules=lists)
    assert classifier.classify(from_addr, subject, name, listed, rules=rulebook.RuleSets(lists)) == said
    assert classifier.classify(from_addr, subject, name, listed, rules=lists) == said      # a plain mapping crosses each call
    assert said[0] in BUCKETS


@settings(max_examples=2000, deadline=None)
@given(st.none() | local_parts | st.text(alphabet=SPACES + EDGES + "".join(TWINS.values()) + "a1", max_size=6))
def test_rust_says_who_looks_human_as_python_did(local):
    assert classifier.looks_human(local) == python_looks_human(local)


@pytest.mark.parametrize("listed", [True, False, None, 0, 1, "", "List-ID", [], ["x"]])
def test_whether_mail_is_listed_is_read_as_python_read_it(listed):
    assert classifier.classify("lin@studio.example.test", "hi", "", listed, rules=NO_RULES) == \
        python_classify("lin@studio.example.test", "hi", "", listed, rules=NO_RULES)


@pytest.mark.parametrize("bucket", [*BUCKETS, None, "", "Spam/Scam", "spam", "blocked ", "junk"])
def test_junk_is_what_it_was(bucket):
    assert classifier.is_junk(bucket) == python_is_junk(bucket)


def test_the_junk_buckets_are_the_ones_the_core_calls_junk():
    assert {b for b in BUCKETS if classifier.is_junk(b)} == classifier.JUNK_BUCKETS == JUNK_BUCKETS


@settings(max_examples=500, deadline=None)
@given(st.none() | st.sampled_from(["kept by rule (sender)", "blocked by rule (domain)", "fraud/MLM pattern",
                                    "unclassified \u2014 review", "by rule", "By rule"]) | st.text(max_size=20))
def test_a_reason_says_a_rule_decided_as_it_did(reason):
    assert classifier.by_rule(reason) == python_by_rule(reason)


# ── the rules cross once ──────────────────────────────────────────────────────────────────────────────────────────

def test_rule_sets_are_still_the_mapping_of_frozensets_they_were():
    sets = rulebook.as_sets({**rulebook.empty(), "keep_senders": ["lin@example.test"], "block_domains": ["example.org"]})
    assert dict(sets) == {"keep_senders": frozenset({"lin@example.test"}), "block_senders": frozenset(),
                          "keep_domains": frozenset(), "block_domains": frozenset({"example.org"})}
    assert {**sets}["keep_senders"] == {"lin@example.test"} and sets.get("filters") is None and len(sets) == 4
    assert isinstance(sets.held, mailmind_leaves.RuleSets)


def test_the_core_gets_the_rules_once_each_time_they_change(data_dir, monkeypatch):
    held, made = mailmind_leaves.RuleSets, []

    def counted(*lists):
        made.append(lists)
        return held(*lists)
    monkeypatch.setattr(mailmind_leaves, "RuleSets", counted)
    put_document("rules", '{"keep_senders": ["lin@example.test"]}')
    for _ in range(3):
        assert curation.classify("lin@example.test", "hi")[2] == "kept by rule (sender)"
    put_document("rules", '{"block_senders": ["lin@example.test"]}')
    for _ in range(3):
        assert curation.classify("lin@example.test", "hi")[2] == "blocked by rule (sender)"
    assert [lists[1] for lists in made] == [frozenset(), frozenset({"lin@example.test"})]


# ── every character there is ──────────────────────────────────────────────────────────────────────────────────────

EVERY = [chr(c) for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF]


def test_every_address_is_stripped_and_lower_cased_as_python_did_it():
    """A sender rule names the address as Python's strip().lower() wrote it. Each probe carries its character's
    number, which lower-casing leaves alone, so a probe can only find its own rule."""
    for start in range(0, len(EVERY), 0x10000):
        probes = [f"{c}{ord(c)}:{c}@x.example{c}" for c in EVERY[start:start + 0x10000]]
        sets = rulebook.RuleSets({**NO_RULES, "keep_senders": [p.strip().lower() for p in probes]})
        missed = [p for p in probes if classifier.classify(p, "", "", False, rules=sets)[2] != "kept by rule (sender)"]
        assert missed == []


def test_every_character_is_a_word_character_or_not_as_python_said():
    """Python's `\\b` sits between a letter, number or `_` and anything else."""
    receipt = TRANSACTIONAL.search
    for c in EVERY:
        for subject in (c + "refund", "refund" + c):
            assert (classifier.classify("lin@example.test", subject, "", False, rules=NO_RULES)[0] == RECEIPT) \
                == bool(receipt(subject)), (hex(ord(c)), subject)


def test_every_character_is_whitespace_or_not_as_python_said():
    for c in EVERY:
        assert (classifier.classify("lin@example.test", c + "Re: lunch", "", False, rules=NO_RULES)[1] == "high") \
            == bool(RE_FWD.match(c + "Re: lunch")), hex(ord(c))


def test_every_character_is_a_digit_or_not_as_python_said():
    for c in EVERY:
        assert classifier.looks_human(c + c + "a") == python_looks_human(c + c + "a"), hex(ord(c))


def test_a_letter_matches_what_it_matched_when_case_is_ignored():
    """Each letter of each strong scam phrase, written as every character whose case leads anywhere near a letter:
    Python matched `i` with a dotted capital I and a dotless small i, `s` with a long s and `k` with the Kelvin
    sign, and no others."""
    letters = set(string.ascii_letters + "éÉ")
    folds = re.compile("[a-zé]", re.I)
    near = [c for c in EVERY if letters & set(c.lower() + c.upper() + c.casefold()) or folds.fullmatch(c)]
    assert {"\u0130", "\u0131", "\u017f", "\u212a", "\ufb00"} <= set(near)
    for phrase in ("lottery", "jackpot", "inheritance", "beneficiar", "western union", "next of kin", "money gram",
                   "wire transfer to", "unclaimed funds", "nigerian prince", "claim your prize", "double your bitcoin",
                   "you have won", "you are a winner"):
        for at, letter in enumerate(phrase):
            if letter.isalpha():
                for c in near:
                    subject = phrase[:at] + c + phrase[at + 1:]
                    assert (classifier.classify("lin@example.test", subject, "", False, rules=NO_RULES)[0] == SPAM) \
                        == bool(STRONG_SCAM.search(subject)), subject
    for c in near:
        subject = f"R{c}f: devis"
        assert classifier.classify("lin@example.test", subject, "", False, rules=NO_RULES) == \
            python_classify("lin@example.test", subject, "", False, rules=NO_RULES), subject


# ── text no Rust string holds ─────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("from_addr, subject, name", [
    ("lin@example.test", "you have won \udce9", ""),
    ("caf\udce9@gmail.com", "make money", ""),
    ("111a\udc80@gmail.com", "hi", ""),          # one character each: 3 digits of 5 is more than half
    ("11a\udc80@gmail.com", "hi", ""),
    ("lin@example.\udce9", "Re: lunch", "\udce9"),
    ("\ud83d\ude00@gmail.com", "\ud83dprofit\ude00!!!", ""),   # a pair of lone surrogates is two characters
])
def test_a_lone_surrogate_reads_as_python_read_it(from_addr, subject, name):
    """An undecodable byte the email package carries as a lone surrogate. The app's header decoding replaces what
    doesn't decode before anything classifies it, but a surrogate still gets Python's answer: the core reads each as
    U+FFFD, one for one, and nothing the classifier asks of a character tells the two apart."""
    assert classifier.classify(from_addr, subject, name, False, rules=NO_RULES) == \
        python_classify(from_addr, subject, name, False, rules=NO_RULES)
    assert classifier.looks_human(from_addr.partition("@")[0]) == python_looks_human(from_addr.partition("@")[0])


def test_a_rule_naming_a_lone_surrogate_names_that_address():
    lists = {**NO_RULES, "keep_senders": frozenset({"caf\udce9@example.test"})}
    assert classifier.classify("Caf\udce9@example.test", "hi", rules=lists) == \
        python_classify("Caf\udce9@example.test", "hi", rules=lists) == ("personal/protected", "high", "kept by rule (sender)")


@pytest.mark.xfail(strict=True, reason="no Rust string holds a lone surrogate, so the core reads each as U+FFFD")
def test_addresses_differing_only_in_a_lone_surrogate_are_told_apart():
    lists = {**NO_RULES, "keep_senders": frozenset({"caf\udce9@example.test"})}
    assert classifier.classify("caf\udcea@example.test", "hi", rules=lists) == \
        python_classify("caf\udcea@example.test", "hi", rules=lists)
