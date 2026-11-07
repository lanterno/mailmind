"""What the other contexts and the app's wiring may ask of Curation."""
from mailmind.curation.domain.classifier import (BLOCKED, JUNK_BUCKETS, MKT, NOTIF, PERSONAL, PROTECTED, RECEIPT, SPAM,
                                                 by_rule, is_junk, looks_human)
from mailmind.curation.domain.expiry import arrival_ts, select_expired
from mailmind.curation.domain.rulebook import EXPIRE, KEYS, decided, domain_listed, expiry_hours

__all__ = ["BLOCKED", "EXPIRE", "JUNK_BUCKETS", "KEYS", "MKT", "NOTIF", "PERSONAL", "PROTECTED", "RECEIPT", "SPAM",
           "arrival_ts", "by_rule", "decided", "domain_listed", "expiry_hours", "is_junk", "looks_human", "select_expired"]
