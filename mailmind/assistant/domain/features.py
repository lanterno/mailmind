"""The AI features, by the kind of call each makes, which decides how the cost governor treats it.

- Interactive: someone asked for it, and is waiting. Everything not named below.
- Background: triage, which keeps summaries up with new mail whether anyone is looking or not.
- Speculative: a reply drafted ahead, made only in case it is wanted.
"""
INTERACTIVE, BACKGROUND, SPECULATIVE = "interactive", "background", "speculative"

_KINDS = {"triage": BACKGROUND, "draft-ahead": SPECULATIVE}


def kind(feature: str) -> str:
    return _KINDS.get(feature, INTERACTIVE)
