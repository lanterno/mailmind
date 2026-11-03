"""Unified mail classifier — single source of truth for MailMind + the curation
scripts. Given an email's From address/name, subject, and whether it carries a
List-Unsubscribe/List-ID header, it returns a bucket, a confidence, and a short
human-readable reason.

Design goals (learned from hand-sweeping six inboxes):
  - Don't rely on List-Unsubscribe alone. Most bulk senders that leaked into
    "personal-ish" did so because they omit that header. Catch them via role
    local-parts (info@, news@, hello@…), ESP/bulk infrastructure domains
    (mailchimp/sendgrid/beehiiv…), and bulk sub-domains (e./mail./news.).
  - Catch outright spam/scam (lottery, MLM, "get paid", crypto-doubling) so it
    never masquerades as a human just because it's sent from a gmail address.
  - Be CONSERVATIVE about keeping: government / education / medical / financial
    senders and real people are protected and never auto-trashed.

Buckets:
  spam/scam                 -> junk, high confidence
  newsletters/marketing     -> junk
  automated-notifications   -> junk
  receipts/transactional    -> KEEP (financial record)
  personal/protected        -> KEEP (gov/edu/medical/finance/known human)
  other/personal-ish        -> KEEP, but the only bucket that still wants a human glance
"""
import re
import os
import json

from mailmind.platform import settings

SPAM = "spam/scam"
BLOCKED = "blocked"                     # user said "always trash this sender/domain"
MKT = "newsletters/marketing"
NOTIF = "automated-notifications"
RECEIPT = "receipts/transactional"
PROTECTED = "personal/protected"
PERSONAL = "other/personal-ish"

JUNK_BUCKETS = {SPAM, BLOCKED, MKT, NOTIF}

# --- learned overrides (a sender/domain allow- & block-list the user grows) ---
_rules_cache = {}


def _rules():
    """Lazy-load mailmind_rules.json (cached by path and mtime so edits are picked up)."""
    path = settings.current().rules
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {"keep_senders": set(), "block_senders": set(),
                "keep_domains": set(), "block_domains": set()}
    if _rules_cache.get("mtime") != mtime or _rules_cache.get("path") != path:
        with open(path) as f:
            raw = json.load(f)
        _rules_cache.clear()
        _rules_cache["path"] = path
        _rules_cache["mtime"] = mtime
        for k in ("keep_senders", "block_senders", "keep_domains", "block_domains"):
            _rules_cache[k] = set(x.lower() for x in raw.get(k, []))
    return _rules_cache


def _domain_listed(domain, listed):
    """True if domain equals or is a sub-domain of any entry in `listed`."""
    return any(domain == d or domain.endswith("." + d) for d in listed)

# --- sender-side signal vocabularies -----------------------------------------
FREE_MAIL = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.co.uk", "outlook.com",
    "live.com", "live.fr", "msn.com", "yahoo.com", "yahoo.co.uk", "yahoo.fr",
    "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com", "gmx.com",
    "gmx.de", "gmx.net", "web.de", "proton.me", "protonmail.com", "pm.me",
    "hey.com", "fastmail.com", "zoho.com", "mail.com", "libero.it", "orange.fr",
    "free.fr", "qq.com", "163.com", "naver.com",
}

# UNAMBIGUOUS marketing local-parts — classify as bulk on their own.
ROLE_MKT = {
    "news", "newsletter", "newsletters", "marketing", "promo", "promotions",
    "promotion", "offers", "deals", "digest", "campaigns", "campaign", "buzz",
    "magazine", "mailings", "mailing", "enews", "discover", "inspiration",
}
# AMBIGUOUS small-business roles — often bulk, but also real small teams. Only
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
    "postmarkapp", "sparkpostmail", "sg.", "everestengagement", "convertkit",
    "ck.page", "drip.com", "activecampaign", "getresponse", "aweber", "emarsys",
    "bluecore", "cordial", "movableink", "narvar", "shopifyemail",
)
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

# Unambiguous fraud — fires regardless of who sent it.
STRONG_SCAM = re.compile(
    r"(you('| ?ha)ve won|you are a winner|claim your (prize|reward|winnings|funds)|"
    r"western union|money ?gram|wire transfer to|inheritance|next of kin|beneficiar|"
    r"lottery|jackpot|unclaimed (funds|money)|nigerian prince|"
    r"\b(viagra|cialis|penis enlarge\w*)\b|"
    r"double your (money|btc|bitcoin|investment))",
    re.I)
# Money-making / MLM / HYIP / urgency — only counts when the SENDER is sketchy
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
# Generic institutional matchers. These are deliberately broad — protect your
# OWN niche institutions (a specific university, bank, clinic, ...) by adding
# their domains to keep_domains in mailmind_rules.json rather than editing here.
GOV_RE = re.compile(r"(\.gov(\.[a-z]{2})?$|\.gouv\.[a-z]{2}$|\.go\.[a-z]{2}$|\.gob\.[a-z]{2}$|"
                    r"\.admin\.[a-z]{2}$|\.gc\.ca$|\.govt\.|\bgov\.)", re.I)
EDU_RE = re.compile(r"(\.edu$|\.edu\.[a-z]{2}$|\.ac\.[a-z]{2}$|\.uni-[a-z]+\.)", re.I)
HEALTH_RE = re.compile(r"(sante|\bsanté|health|clinic|hospital|\bmedi|\bmedic|patient|doctolib|dentist)", re.I)
FINANCE_RE = re.compile(r"(\bbank\b|\bbanque\b|paypal|wise\.com|revolut|\bn26\b|"
                        r"stripe\.com$|\bvisa\b|mastercard)", re.I)


def _split(addr):
    addr = (addr or "").strip().lower()
    if "@" not in addr:
        return "", ""
    local, _, domain = addr.partition("@")
    return local, domain


def _is_protected_domain(domain, local):
    if GOV_RE.search(domain) or EDU_RE.search(domain):
        return True
    # health / finance: protect unless it's an obvious marketing stream
    if (HEALTH_RE.search(domain) or FINANCE_RE.search(domain)) and local not in ROLE_MKT:
        return True
    return False


def _looks_human(local):
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


def _is_scam(local, domain, subject, name):
    tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
    blob = f"{subject} {name}"
    if STRONG_SCAM.search(blob):
        return True
    sketchy = domain in FREE_MAIL or tld in SUSPICIOUS_TLDS
    if sketchy and WEAK_SCAM.search(blob):
        return True
    # sketchy sender + money + loud styling (!!! or SHOUTING) — classic blast
    if sketchy and MONEY.search(subject) and (subject.count("!") >= 3 or SHOUTY.search(subject)):
        return True
    return False


def classify(from_addr, subject="", from_name="", has_list_unsub=False):
    """Return (bucket, confidence in {'high','med','low'}, reason)."""
    subject = subject or ""
    name = from_name or ""
    local, domain = _split(from_addr)
    label0 = domain.split(".")[0] if domain else ""
    nlabels = domain.count(".") + 1 if domain else 0
    full = from_addr.strip().lower()

    # 0) learned overrides win over all heuristics — the user's explicit verdicts.
    #    Exact-sender rules beat domain rules (a blocked noreply@ overrides a
    #    kept whole-domain), and within each scope keep beats block.
    rules = _rules()
    if full in rules["keep_senders"]:
        return PROTECTED, "high", "kept by rule (sender)"
    if full in rules["block_senders"]:
        return BLOCKED, "high", "blocked by rule (sender)"
    if _domain_listed(domain, rules["keep_domains"]):
        return PROTECTED, "high", "kept by rule (domain)"
    if _domain_listed(domain, rules["block_domains"]):
        return BLOCKED, "high", "blocked by rule (domain)"

    # 1) spam/scam wins over the remaining heuristics (incl. gmail-sent scams)
    if _is_scam(local, domain, subject, name):
        return SPAM, "high", "fraud/MLM pattern"

    # 2) protected senders — keep, never auto-trash (unless it was scam above)
    if _is_protected_domain(domain, local):
        return PROTECTED, "high", "government/education/medical/financial domain"

    # 3) genuine financial records (subject wording OR a billing-ish local-part,
    #    incl. things like failed-payments@ / invoice+statements@)
    if (TRANSACTIONAL.search(subject) or local in ROLE_TXN
            or any(t in local for t in ("receipt", "invoice", "billing", "payment", "statement"))):
        return RECEIPT, "med", "transactional/receipt signal"

    # 4) newsletters / marketing — the big leak we now close
    if has_list_unsub:
        return MKT, "high", "List-Unsubscribe header"
    if any(m in domain for m in ESP_MARKERS):
        return MKT, "high", "bulk email-service-provider domain"
    if nlabels >= 3 and label0 in BULK_SUBDOMAINS:
        return MKT, "med", f"bulk sub-domain ({label0}.)"

    # A genuine Re:/Fwd: reply that survived the hard bulk checks above is a real
    # human thread — keep it rather than letting a role address downgrade it.
    if RE_FWD.match(subject) and local not in ROLE_NOTIF:
        return PERSONAL, "high", "reply/forward in an active thread"

    if domain not in FREE_MAIL and local in ROLE_MKT:
        return MKT, "med", f"marketing role address ({local}@)"

    # 5) automated notifications
    if domain not in FREE_MAIL and (local in ROLE_NOTIF or NOTIF_RE.search(local)):
        return NOTIF, "med", "no-reply / notification address"

    # 6) a real person from a personal mailbox
    if domain in FREE_MAIL and _looks_human(local):
        return PERSONAL, "med", "person on a personal mailbox"

    # 7) genuinely ambiguous — keep, flag for a human glance
    return PERSONAL, "low", "unclassified — review"


NOTIF_RE = re.compile(r"(no-?reply|do-?not-?reply|notification|notify|mailer-daemon|automated|noreply)", re.I)
RE_FWD = re.compile(r"^\s*(re|fwd|fw|aw|wg|tr|rv|r[ée]f)\s*:", re.I)  # reply/forward prefixes (multi-lingual)


def is_junk(bucket_name):
    return bucket_name in JUNK_BUCKETS
