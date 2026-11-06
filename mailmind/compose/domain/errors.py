"""What Compose raises for its callers to tell apart."""


class Refused(Exception):
    """A request that can't be sent or saved as it stands, and the answer that says so."""
    def __init__(self, code: int, msg: str):
        self.code, self.msg = code, msg


class Unanswered(ConnectionError):
    """The whole message went out and no answer came, so it may have been delivered: sent again it
    could reach its recipients twice. That's for a person to decide."""
