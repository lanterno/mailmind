"""Curation's wiring: the one place that binds its ports to their adapters and, until those contexts move,
to the legacy modules behind filing and sending. The legacy modules reach Curation through here."""
from mailmind.curation.adapters import rules_file as rules

__all__ = ["rules"]
