"""The AI providers a person may choose (#49). Here because a person's preferences keep the one they chose and Accounts keeps
each one's key, while the Assistant talks to it, and neither context imports the other. A request's schema takes them as
`AiProvider`, so the page has them as a type and keeps only its words for each."""
from typing import Literal, get_args

type AiProvider = Literal["anthropic", "openai", "gemini", "local", "compatible"]     # local: a model on this Mac
AI_PROVIDERS: tuple[AiProvider, ...] = get_args(AiProvider.__value__)
DEFAULT_PROVIDER: AiProvider = "anthropic"
