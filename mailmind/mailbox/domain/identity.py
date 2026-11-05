"""What makes two rows copies of one message: the same account and the same identity.

A row is one copy in one folder, and a row doesn't last. A move deletes it, and a new UIDVALIDITY
renumbers it. The Message-ID outlives both, written without the whitespace a sender can fold into it.
A message with none is known by its envelope, which is all two copies of it share.
"""
from hashlib import sha256


def of(message_id, from_addr, date_iso, subject):
    written = "".join((message_id or "").split())
    if written:
        return written
    return "~" + sha256("|".join((from_addr or "", date_iso or "", subject or "")).encode()).hexdigest()
