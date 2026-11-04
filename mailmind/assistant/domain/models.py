"""What each Claude model takes: an effort setting, and the server-side refusal fallback."""

DEFAULT = "claude-opus-5-5"        # every feature's model unless MAILMIND_MODEL names another
EFFORT = "medium"                  # sent explicitly: models differ in their default
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Models that accept `fallbacks: "default"`: a classifier decline is re-run server-side on the
# model Anthropic recommends for that category instead of coming back as a refusal. Never Haiku,
# which has no server-side fallback.
_FALLBACK = frozenset({"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"})
# Models older than output_config.effort, which reject it (MAILMIND_MODEL may name one).
_NO_EFFORT = ("claude-haiku-4-5", "claude-sonnet-4-5")


def takes_effort(model: str) -> bool:
    return not model.startswith(_NO_EFFORT)


def takes_fallback(model: str) -> bool:
    return model in _FALLBACK
