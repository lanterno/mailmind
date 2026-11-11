"""A snooze names at most `BULK_CAP` copies. One naming more is the schema's 400 and snoozes none of them, never the first 500 of
them with the rest left as they were, since the page has already taken every marked row off the list. The page sends more as
more requests, under one undo group."""
from mailmind.attention.domain.snooze import BULK_CAP


def test_a_planned_snooze_never_names_more_than_one_snooze_takes():
    """A bulk command's confirm snoozes every message its plan froze in one call (`Filing.carry_out`), so the plan's ceiling
    must stay within the snooze's, or raising it would turn a planned snooze into a refusal."""
    from mailmind.assistant.domain.action_plan import CAP
    assert CAP <= BULK_CAP
