"""Curation's wiring: the one place that binds its ports to their adapters and, until those contexts move,
to the legacy modules behind filing and sending. The legacy modules reach Curation through here."""
from mailmind.curation.adapters import rules_file as rules
from mailmind.curation.api import (BLOCKED, EXPIRE, JUNK_BUCKETS, KEYS, MKT, NOTIF, PERSONAL, PROTECTED, RECEIPT, SPAM,
                                   by_rule, decided, domain_listed, expiry_hours, is_junk, looks_human)
from mailmind.curation.domain import classifier

__all__ = ["BLOCKED", "EXPIRE", "JUNK_BUCKETS", "KEYS", "MKT", "NOTIF", "PERSONAL", "PROTECTED", "RECEIPT", "SPAM",
           "by_rule", "classify", "decided", "domain_listed", "expiry_hours", "is_junk", "looks_human", "rules"]


def classify(from_addr, subject="", from_name="", has_list_unsub=False):
    """The classifier over the rule book as it stands."""
    return classifier.classify(from_addr, subject, from_name, has_list_unsub, rules=rules.sets())
