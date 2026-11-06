"""When a draft's copy goes to its server (decision D5): a save is local, and a push follows it on close, on an explicit
save and at most every PUSH_EVERY seconds while someone types."""
PUSH_EVERY = 60
BACKOFF_FIRST = 20
BACKOFF_MOST = 900


def due_after_typing(now, pushed_at):
    """When an autosave's copy may go: PUSH_EVERY after the last push, or after this first save."""
    return max(now, pushed_at + PUSH_EVERY) if pushed_at else now + PUSH_EVERY


def due_after_failing(now, attempts):
    """A push that failed goes again later and later. The bytes wait here, so nothing is ever given up."""
    return now + min(BACKOFF_MOST, BACKOFF_FIRST * 2 ** min(attempts, 10))
