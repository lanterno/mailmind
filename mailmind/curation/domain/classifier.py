"""The mail classifier. Given an email's From address and name, its subject, and whether it carries a
List-Unsubscribe or List-ID header, it returns a bucket, a confidence, and a short human-readable reason.

The Rust core decides (`mm_curation::classifier`), as every client does, and answers as the Python
classifier it replaced did, but where the core has fixed a pattern since: the suite holds it to that
classifier, with each fix. Its documentation says what hand-sweeping six inboxes taught it.

Buckets:
  spam/scam                 -> junk, high confidence
  blocked                   -> junk: the person said always to trash this sender or domain
  newsletters/marketing     -> junk
  automated-notifications   -> junk
  receipts/transactional    -> KEEP (financial record)
  personal/protected        -> KEEP (gov/edu/medical/finance/known human)
  other/personal-ish        -> KEEP, but the only bucket that still wants a human glance
"""
from collections.abc import Collection, Mapping

import mailmind_leaves

from mailmind.curation.domain.rulebook import RuleSets

SPAM = "spam/scam"
BLOCKED = "blocked"                     # user said "always trash this sender/domain"
MKT = "newsletters/marketing"
NOTIF = "automated-notifications"
RECEIPT = "receipts/transactional"
PROTECTED = "personal/protected"
PERSONAL = "other/personal-ish"

JUNK_BUCKETS = {SPAM, BLOCKED, MKT, NOTIF}


def classify(from_addr: str, subject: str | None = "", from_name: str | None = "", has_list_unsub: bool = False, *,
             rules: Mapping[str, Collection[str]]) -> tuple[str, str, str]:
    """Return (bucket, confidence in {'high','med','low'}, reason). `rules` is the rule book's keep and
    block lists: a RuleSets, which the core already holds, or any mapping of the four, sent across each call."""
    held = rules.held if isinstance(rules, RuleSets) else RuleSets(rules).held
    return mailmind_leaves.classify(from_addr, subject or "", from_name or "", bool(has_list_unsub), held)


def looks_human(local: str | None) -> bool:
    """A local-part that looks like a person, not a role or a random mailer id."""
    return mailmind_leaves.looks_human(local)


def by_rule(reason: str | None) -> bool:
    """Whether `reason`, as classify gave it, says one of the person's rules decided."""
    return mailmind_leaves.by_rule(reason)


def is_junk(bucket_name: str | None) -> bool:
    return mailmind_leaves.is_junk(bucket_name)
