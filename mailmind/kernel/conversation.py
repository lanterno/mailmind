"""How many messages a conversation holds, as a list's badge says it. Standard library only."""


def count_sql(t: str = "m") -> str:
    """The messages of the conversation that row `t` of `messages` is in, as opening it shows them (`thread_rows`, then
    `Queries.thread_json`): in every folder of its account, each Message-ID once, a copy with none alone, none on its way out."""
    return (f"(SELECT COUNT(DISTINCT COALESCE(NULLIF(TRIM(tm.message_id), ''), 'pk:' || tm.pk)) FROM messages tm "
            f"WHERE tm.account={t}.account AND tm.thread_id={t}.thread_id AND tm.pending_removal IS NULL)")
