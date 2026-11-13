"""The leaves the core shares with the Python app (core/crates/mm-py)."""

def identity_of(message_id: str | None, from_addr: str | None, date_iso: str | None, subject: str | None) -> str:
    """A message's identity: its Message-ID without whitespace, or `~` and the SHA-256 of its envelope."""
    ...
