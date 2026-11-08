"""What the other contexts and the app's wiring may ask of Curation, and what a request may name: a rule's verdict and how far a
cleanup's block goes."""
from mailmind.curation.domain.classifier import (BLOCKED, JUNK_BUCKETS, MKT, NOTIF, PERSONAL, PROTECTED, RECEIPT, SPAM,
                                                 by_rule, is_junk, looks_human)
from mailmind.curation.domain.cleanup_plan import BlockScope
from mailmind.curation.domain.expiry import arrival_ts, select_expired
from mailmind.curation.domain.filters import FilterVerb
from mailmind.curation.domain.rulebook import EXPIRE, KEYS, RuleChange, decided, domain_listed, expiry_hours

__all__ = ["BLOCKED", "BlockScope", "EXPIRE", "FilterVerb", "JUNK_BUCKETS", "KEYS", "MKT", "NOTIF", "PERSONAL", "PROTECTED", "RECEIPT", "RuleChange", "SPAM",
           "arrival_ts", "by_rule", "decided", "domain_listed", "expiry_hours", "is_junk", "looks_human", "select_expired"]
