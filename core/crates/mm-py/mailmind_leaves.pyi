"""The leaves the core shares with the Python app (core/crates/mm-py)."""

# -- message identity (mm-kernel) ---------------------------------------------------------------------------------

def identity_of(message_id: str | None, from_addr: str | None, date_iso: str | None, subject: str | None) -> str:
    """A message's identity: its Message-ID without whitespace, or `~` and the SHA-256 of its envelope."""
    ...


# -- the search grammar (mm-discovery, src/query.rs) ---------------------------------------------------------------



# -- the classifier (mm-curation, src/classifier.rs) ---------------------------------------------------------------



# -- threading (mm-mailbox, src/threading.rs) ----------------------------------------------------------------------
