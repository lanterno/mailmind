"""Whether a stored copy of a message is snoozed, as every query that lists mail asks it. `snoozes` says, by the message's
key and the folder: any copy in that folder is hidden, however it got there, and one filed elsewhere isn't."""


def snoozed_sql(t: str = "m") -> str:
    """A row of `messages` named `t` is snoozed. It goes from `snoozes`, which holds little, so a query with nothing else
    to narrow it needn't read all the mail."""
    return f"({t}.message_key, {t}.folder) IN (SELECT message_key, folder FROM snoozes)"


def not_snoozed_sql(t: str = "m") -> str:
    """A row of `messages` named `t` isn't. One with no key isn't snoozed, where a NOT IN would leave it out of every list."""
    return f"NOT EXISTS (SELECT 1 FROM snoozes z WHERE z.message_key={t}.message_key AND z.folder={t}.folder)"
