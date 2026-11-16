"""The AI providers a person may choose (#49). Here because a person's preferences keep the one they chose and Accounts keeps
each one's key, while the Assistant talks to it, and neither context imports the other. A request's schema takes them as
`AiProvider`, so the page has them as a type and keeps only its words for each."""
from typing import Literal, get_args

type AiProvider = Literal["anthropic", "openai", "gemini", "ollama", "compatible"]
AI_PROVIDERS: tuple[AiProvider, ...] = get_args(AiProvider.__value__)
DEFAULT_PROVIDER: AiProvider = "anthropic"
KEYLESS: frozenset[AiProvider] = frozenset({"ollama"})        # takes no key: none is ever sent or kept
KEYED: tuple[AiProvider, ...] = tuple(p for p in AI_PROVIDERS if p not in KEYLESS)
