"""The AI features, by the kind of call each makes, which decides how the cost governor treats it.

- Interactive: someone asked for it, and is waiting. Everything not named below.
- Background: triage, which keeps summaries up with new mail whether anyone is looking or not,
  triage-batch, the same for a backlog through the Message Batches API, and delta, the line on what came in since you
  last looked, which the page asks for on its own.
- Speculative: a reply drafted ahead, made only in case it is wanted.
- Scheduled: what a person turned on in Settings and doesn't wait for: the morning's brief, written ahead of the day's first open,
  the week's review, written ahead on Monday morning, and Tidy's daily run, which spends nothing but is held to the same bars.
"""
INTERACTIVE, BACKGROUND, SPECULATIVE, SCHEDULED = "interactive", "background", "speculative", "scheduled"

_KINDS = {"triage": BACKGROUND, "triage-batch": BACKGROUND, "delta": BACKGROUND, "draft-ahead": SPECULATIVE,
          "brief-ahead": SCHEDULED, "review-ahead": SCHEDULED, "auto-tidy": SCHEDULED}


def kind(feature: str) -> str:
    return _KINDS.get(feature, INTERACTIVE)
