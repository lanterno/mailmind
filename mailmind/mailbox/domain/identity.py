"""What makes two rows copies of one message: the same account and the same identity.

A row is one copy in one folder, and a row doesn't last. A move deletes it, and a new UIDVALIDITY
renumbers it. The Message-ID outlives both, written without the whitespace a sender can fold into it.
A message with none is known by its envelope, which is all two copies of it share.

The Rust core computes it (`mm_kernel::identity::of`), as every client does, so one written here names the same
message there: `of(message_id, from_addr, date_iso, subject)`.
"""
from mailmind_leaves import identity_of as of

__all__ = ["of"]
