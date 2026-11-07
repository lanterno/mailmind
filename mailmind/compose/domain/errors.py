"""What Compose raises for its callers to tell apart."""


class Unanswered(ConnectionError):
    """The whole message went out and no answer came, so it may have been delivered: sent again it
    could reach its recipients twice. That's for a person to decide."""
