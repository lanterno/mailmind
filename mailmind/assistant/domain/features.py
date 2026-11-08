"""The AI features, by the kind of call each makes, which decides how the cost governor treats it.

- Interactive: someone asked for it, and is waiting. Everything not named below.
- Background: triage, which keeps summaries up with new mail whether anyone is looking or not, and
  triage-batch, the same for a backlog through the Message Batches API.
- Speculative: a reply drafted ahead, made only in case it is wanted.
- Scheduled: what a person turned on in Settings and doesn't wait for: the morning's brief, written ahead of the day's first open.
"""
INTERACTIVE, BACKGROUND, SPECULATIVE, SCHEDULED = "interactive", "background", "speculative", "scheduled"

_KINDS = {"triage": BACKGROUND, "triage-batch": BACKGROUND, "draft-ahead": SPECULATIVE, "brief-ahead": SCHEDULED}


def kind(feature: str) -> str:
    return _KINDS.get(feature, INTERACTIVE)
