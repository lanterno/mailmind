"""What the rest of the app may name of the Mailbox without its wiring: what a request to file mail may name, and how many
rows one may. What it does, it does through the use cases in mailmind.bootstrap.mailbox."""
from mailmind.mailbox.domain.filing import BULK_MAX, FilingVerb, FlagName

__all__ = ["BULK_MAX", "FilingVerb", "FlagName"]
