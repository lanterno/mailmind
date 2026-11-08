"""The tones a reply may be drafted in. Here because the Assistant drafts in one (`prompts.TONES`) and a person's preferences keep
the one they chose (Accounts), and neither context imports the other. A request's schema takes them as `DraftTone`, so the page has
them as a type and keeps only its words for each."""
from typing import Literal

type DraftTone = Literal["brief", "balanced", "warm", "formal"]
DEFAULT_TONE: DraftTone = "balanced"
