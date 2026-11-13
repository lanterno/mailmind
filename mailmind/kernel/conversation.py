"""How many messages a conversation holds, as a list's badge says it. Standard library only."""


def count_sql(t: str = "m") -> str:
    """The messages of the conversation that row `t` of `messages` is in, as opening it shows them (`thread_rows`, then
    `Queries.thread_json`): in every folder of its account, each Message-ID once, a copy with none alone, none on its way out."""
    return (f"(SELECT COUNT(DISTINCT COALESCE(NULLIF(TRIM(tm.message_id), ''), 'pk:' || tm.pk)) FROM messages tm "
            f"WHERE tm.account={t}.account AND tm.thread_id={t}.thread_id AND tm.pending_removal IS NULL)")


def latest_sql(t: str = "m") -> str:
    """Whether row `t` of `messages` is the latest of its conversation in its account, as its list shows the conversation:
    the newest, the higher pk on a tie, of those not on their way out."""
    return (f"{t}.pk=(SELECT pk FROM messages m2 WHERE m2.account={t}.account AND m2.thread_id={t}.thread_id "
            f"AND m2.pending_removal IS NULL ORDER BY m2.ts DESC, m2.pk DESC LIMIT 1)")


def has_mail_sql(t: str) -> str:
    """Whether the conversation row `t` names (by account and thread id) still has a message not on its way out: what its
    list shows a row for, and its badge counts."""
    return (f"EXISTS(SELECT 1 FROM messages m WHERE m.account={t}.account AND m.thread_id={t}.thread_id "
            f"AND m.pending_removal IS NULL)")
