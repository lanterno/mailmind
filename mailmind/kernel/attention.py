"""What the AI's verdict on a message asks of you. Each question has its one answer here: the
queries and the routes ask these, and the page reads what the routes say instead of working it out.

A verdict is the importance (1 to 5), the category and the next step the AI wrote. A message with no
verdict yet has none of them, and asks nothing of you."""

ACTION_NEEDED = "action-needed"


def _has_step(action):
    return bool((action or "").strip())


def reply_worthy(importance, category):
    """Worth drafting a reply to ahead of time. A next step alone isn't: it never spends a call."""
    return (importance or 0) >= 4 or category == ACTION_NEEDED


def needs_you(importance, category, action):
    """The to-do test: a next step, an action-needed category, or importance 4 and up."""
    return _has_step(action) or reply_worthy(importance, category)


def commitment_worthy(importance, category, action):
    """A next step that belongs on the agenda, not every low-priority nudge."""
    return _has_step(action) and ((importance or 0) >= 3 or category == ACTION_NEEDED)


def safe_to_file(importance, category, action):
    """Noise Tidy offers to file: low importance, no next step, plain info or other. Never a
    newsletter, which lives in the Feed, or anything asked of you. Filing is reversible, so a message
    whose importance is missing counts as low."""
    return (importance or 1) <= 2 and not _has_step(action) and category in ("info", "other")


def answers(importance, category, action):
    """All four, as a list row in the API carries them."""
    return {"needs_you": needs_you(importance, category, action),
            "reply_worthy": reply_worthy(importance, category),
            "commitment_worthy": commitment_worthy(importance, category, action),
            "safe_to_file": safe_to_file(importance, category, action)}


def reply_worthy_sql(s="s"):
    """`reply_worthy` over a summaries row named `s`, or its bare columns if `s` is empty."""
    p = f"{s}." if s else ""
    return f"({p}importance>=4 OR {p}category='{ACTION_NEEDED}')"


def needs_you_sql(s="s"):
    """`needs_you` over a summaries row named `s`, or its bare columns if `s` is empty."""
    p = f"{s}." if s else ""
    return f"({p}importance>=4 OR {p}category='{ACTION_NEEDED}' OR TRIM(COALESCE({p}action,''))!='')"
